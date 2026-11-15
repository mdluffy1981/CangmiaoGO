"""Bounded local KataGo benchmark and non-destructive configuration tuning."""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import statistics
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import paths
from .engine.gtp import GtpParseError, ResponseAssembler, parse_response
from .engine.launch import OVERRIDES
from .go.coords import from_gtp
from .i18n_tuning import tt
from .settings import Settings

TOTAL_TIMEOUT = 300.0
COMMAND_TIMEOUT = 45.0
MIN_GAIN = 1.05
VISITS = 32
REPEATS = 2
_THREAD_LINE = re.compile(r"(?m)^[ \t]*numSearchThreads[ \t]*=[ \t]*(\d+)[ \t]*(?:#.*)?$")
_RESULT = re.compile(r"numSearchThreads\s*=\s*(\d+):\s*(\d+)\s*/\s*(\d+)\s+positions,\s*visits/s\s*=\s*([0-9]+(?:\.[0-9]+)?)")


class TuningError(RuntimeError):
    pass


class TuningCancelled(TuningError):
    pass


@dataclass(frozen=True)
class TuningResult:
    config: str | None
    baseline: float
    best: float
    threads: int
    log: str


def _failure(code: int, output: str) -> str:
    lower = output.lower()
    if any(s in lower for s in ("out of memory", "bad_alloc", "alloc_failed", "allocation failed")):
        kind = tt("内存不足")
    elif code in (-1073741515, -1073741511, 0xC0000135, 0xC0000139) or "dll" in lower and ("missing" in lower or "not found" in lower):
        kind = tt("缺少引擎运行库或 DLL")
    elif any(s in lower for s in ("no cuda", "no device", "no gpu", "device not found", "cuda error", "opencl error")):
        kind = tt("后端或设备不可用")
    else:
        kind = tt("引擎失败（配置、模型或进程错误）")
    return tt("{0}；退出码 {1}。{2}", kind, code, output[-1800:])


def _config_with_threads(text: str, threads: int) -> str:
    matches = list(_THREAD_LINE.finditer(text))
    if len(matches) != 1:
        raise TuningError(tt("配置必须且只能包含一个显式整数 numSearchThreads 设置。"))
    match = matches[0]
    return text[:match.start(1)] + str(threads) + text[match.end(1):]


def _benchmark_value(output: str, threads: int) -> float:
    matches = [m for m in _RESULT.finditer(output)
               if int(m.group(1)) == threads and m.group(2) == m.group(3) and int(m.group(3)) > 0]
    if not matches:
        raise TuningError(tt("基准测试没有生成完整且可解析的 visits/s 结果。"))
    value = float(matches[-1].group(4))
    if not math.isfinite(value) or value <= 0:
        raise TuningError(tt("基准测试返回了无效的 visits/s 结果。"))
    return value


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_identity(path: str) -> dict[str, int]:
    stat = Path(path).stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def recent_result(settings: Settings) -> str:
    """Summarize the last successful measurement and detectable environment changes."""
    try:
        report = json.loads((paths.app_home() / "tuning" / "recent.json").read_text(encoding="utf-8"))
        es = settings.engine
        stale = (str(Path(es.executable).resolve()) != report["executable"]
                 or str(Path(es.model).resolve()) != report["model"]
                 or str(Path(es.config).resolve()) != report["config"]
                 or settings.game.board_size != report["board_size"]
                 or platform.platform() != report["platform"]
                 or platform.processor() != report["processor"]
                 or _file_identity(es.executable) != report["executable_stat"]
                 or _file_identity(es.model) != report["model_stat"]
                 or _file_identity(es.config) != report["config_stat"])
        state = tt("已过期，请重新测试") if stale else tt("文件和环境标识一致")
        return tt("最近测试：{0}，{1:.2f} → {2:.2f} visits/s（{3}）。GPU 或设备变化可能需要重新测试。日志：{4}",
                  report["backend"], report["baseline"], report["best"], state, report["log"])
    except (OSError, ValueError, TypeError, KeyError):
        return tt("暂无最近调优测试结果。")


class Tuner:
    """Run in a worker thread. Call cancel from any thread."""

    def __init__(self, settings: Settings, message: Callable[[str], None] = lambda _: None):
        self.settings = settings
        self.message = message
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._deadline = 0.0
        self._log: list[str] = []

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        with self._lock:
            self._cancel.set()
            if self._proc is not None and self._proc.poll() is None:
                self._proc.kill()

    def _check(self) -> None:
        if self._cancel.is_set():
            raise TuningCancelled(tt("调优已取消，原配置保持不变。"))
        if time.monotonic() >= self._deadline:
            raise TuningError(tt("调优超过总时限，原配置保持不变。"))

    def _say(self, text: str) -> None:
        self._log.append(text)
        self.message(text)

    def _run(self, args: list[str], input_text: str | None = None) -> str:
        self._check()
        self._say(tt("命令：{0}", subprocess.list2cmdline(args)))
        if input_text:
            self._log.append(tt("GTP 输入：\n{0}", input_text))
        try:
            with self._lock:
                self._check()
                proc = subprocess.Popen(args, cwd=str(paths.engine_work_dir()), stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                        encoding="utf-8", errors="replace")
                self._proc = proc
            deadline = min(self._deadline, time.monotonic() + COMMAND_TIMEOUT)
            pending = input_text
            while True:
                self._check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TuningError(tt("引擎命令超时，原配置保持不变。"))
                try:
                    stdout, stderr = proc.communicate(input=pending, timeout=min(0.25, remaining))
                    break
                except subprocess.TimeoutExpired:
                    pending = None
            output = (stdout or "") + "\n" + (stderr or "")
            self._log.append(output)
            self._check()
            if proc.returncode:
                raise TuningError(_failure(proc.returncode, output))
            return output
        except OSError as exc:
            raise TuningError(tt("引擎不可用或无法启动：{0}", exc)) from exc
        finally:
            with self._lock:
                proc = self._proc
                self._proc = None
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.communicate()

    def _self_check(self, exe: str, model: str, config: Path) -> None:
        game = self.settings.game
        commands = ["protocol_version", "name", f"boardsize {game.board_size}", "clear_board",
                    f"kata-set-rules {game.rules}", f"komi {game.komi}",
                    "kata-set-param maxVisits 16", "kata-set-param maxTime 5", "genmove B", "quit"]
        payload = "".join(f"{i} {cmd}\n" for i, cmd in enumerate(commands, 1))
        output = self._run([exe, "gtp", "-model", model, "-config", str(config),
                            "-override-config", OVERRIDES], payload)
        assembler = ResponseAssembler()
        responses = []
        try:
            for line in output.splitlines():
                block = assembler.feed(line)
                if block:
                    responses.append(parse_response(block))
        except GtpParseError as exc:
            raise TuningError(tt("配置 GTP 自检返回无效响应：{0}", exc)) from exc
        if len(responses) != len(commands) or any(
                not r.ok or r.id != i for i, r in enumerate(responses, 1)):
            raise TuningError(tt("配置 GTP 自检失败或响应不完整。"))
        if responses[-2].text.lower() != "resign":
            try:
                from_gtp(responses[-2].text, game.board_size)
            except ValueError as exc:
                raise TuningError(tt("配置 GTP 自检返回无效着手。")) from exc

    def run(self) -> TuningResult:
        self._deadline = time.monotonic() + TOTAL_TIMEOUT
        start = time.monotonic()
        directory = paths.app_home() / "tuning"
        directory.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex
        log_path = directory / f"{run_id}.log"
        candidate: Path | None = None
        applied = False
        try:
            es = self.settings.engine
            missing = es.missing()
            if missing:
                raise TuningError(tt("引擎文件不可用：{0}", "；".join(missing)))
            self.settings.game.validate()
            if not 7 <= self.settings.game.board_size <= 19:
                raise TuningError(tt("基准测试仅支持 7–19 路棋盘。"))
            exe, model, source = (str(Path(p).resolve()) for p in (es.executable, es.model, es.config))
            text = Path(source).read_text(encoding="utf-8-sig")
            source_hash = _sha(Path(source))
            executable_hash = _sha(Path(exe))
            model_hash = _sha(Path(model))
            executable_stat, model_stat = _file_identity(exe), _file_identity(model)
            self._check()
            match = list(_THREAD_LINE.finditer(text))
            if len(match) != 1:
                raise TuningError(tt("配置必须且只能包含一个显式整数 numSearchThreads 设置。"))
            baseline_threads = int(match[0].group(1))
            if not 1 <= baseline_threads <= 256:
                raise TuningError(tt("当前 numSearchThreads 超出安全测试范围（1–256）。"))
            self._say(tt("正在检查引擎版本和支持的基准测试选项。"))
            version = self._run([exe, "version"])
            backend = re.search(r"Using\s+(Eigen(?:\(CPU\))?|CUDA|OpenCL)\s+backend", version, re.I)
            if not backend:
                raise TuningError(tt("不支持或无法识别的 KataGo 后端，未更改设置。"))
            help_text = self._run([exe, "help"])
            if not re.search(r"(?m)^\s*benchmark\s*:", help_text):
                raise TuningError(tt("该引擎未声明支持 benchmark。"))
            bench_help = self._run([exe, "benchmark", "-help"])
            required = ("-config", "-model", "-v", "-t", "-n", "-boardsize", "-override-config",
                        "-no-server-thread-test", "-no-half-batch-size-test")
            if any(not re.search(re.escape(flag) + r"(?![\w-])", bench_help) for flag in required):
                raise TuningError(tt("引擎基准测试选项不兼容，未更改设置。"))
            self._say(tt("后端：{0}；棋盘：{1}；模型：{2}；预热后采样 {3} 次，每次 {4} visits。",
                         backend.group(1), self.settings.game.board_size, model, REPEATS, VISITS))
            candidates = [baseline_threads]
            cap = min(16, max(1, os.cpu_count() or 1))
            for value in (min(cap, max(1, baseline_threads // 2)), min(cap, baseline_threads * 2)):
                if value not in candidates:
                    candidates.append(value)
            results: dict[int, float] = {}
            for threads in candidates:
                samples = []
                for sample in range(REPEATS + 1):
                    phase = tt("预热") if sample == 0 else tt("采样 {0}/{1}", sample, REPEATS)
                    self._say(tt("线程 {0}：{1}（已用 {2:.1f} 秒）", threads, phase, time.monotonic() - start))
                    out = self._run([exe, "benchmark", "-config", source, "-model", model,
                                     "-v", str(VISITS), "-n", "1", "-t", str(threads),
                                     "-boardsize", str(self.settings.game.board_size),
                                     "-override-config", OVERRIDES, "-no-server-thread-test",
                                     "-no-half-batch-size-test"])
                    value = _benchmark_value(out, threads)
                    if sample:
                        samples.append(value)
                    self._say(tt("实测 {0:.2f} visits/s", value))
                results[threads] = statistics.median(samples)
            baseline = results[baseline_threads]
            best_threads = max(results, key=results.get)
            best = results[best_threads]
            self._say(tt("调优前中位数：{0:.2f} visits/s；最佳：{1:.2f} visits/s（{2} 线程）。不保证性能提升。",
                         baseline, best, best_threads))
            self._check()
            if (source_hash != _sha(Path(source)) or executable_hash != _sha(Path(exe))
                    or model_hash != _sha(Path(model))):
                raise TuningError(tt("调优期间引擎、模型或配置已变化，不应用结果。"))
            report = {"backend": backend.group(1), "baseline": baseline, "best": best,
                      "executable": exe, "model": model, "config": source,
                      "executable_stat": executable_stat, "model_stat": model_stat,
                      "config_stat": _file_identity(source), "board_size": self.settings.game.board_size,
                      "platform": platform.platform(), "processor": platform.processor(), "log": str(log_path)}
            if best_threads == baseline_threads or best < baseline * MIN_GAIN:
                paths.atomic_write_text(directory / "recent.json", json.dumps(report, ensure_ascii=False, indent=2))
                self._say(tt("没有显著收益（至少 5%），原配置保持不变。"))
                return TuningResult(None, baseline, best, best_threads, str(log_path))
            candidate = directory / f"config-{run_id}.cfg"
            paths.atomic_write_text(candidate, _config_with_threads(text, best_threads))
            self._say(tt("正在用 GTP 和短搜索验证配置。"))
            self._self_check(exe, model, candidate)
            self._check()
            if (source_hash != _sha(Path(source)) or executable_hash != _sha(Path(exe))
                    or model_hash != _sha(Path(model))):
                raise TuningError(tt("调优期间引擎、模型或配置已变化，不应用结果。"))
            backup = directory / f"backup-{run_id}.cfg"
            paths.atomic_write_text(backup, text)
            metadata = {
                "previous_config": source, "backup": str(backup), "config": str(candidate),
                "backend": backend.group(1), "version": version.strip(), "platform": platform.platform(),
                "processor": platform.processor(), "executable": exe, "executable_sha256": executable_hash,
                "model": model, "model_sha256": model_hash, "config_sha256": source_hash,
                "board_size": self.settings.game.board_size,
                "baseline": baseline, "best": best, "threads": best_threads, "log": str(log_path),
            }
            with self._lock:
                self._check()
                paths.atomic_write_text(directory / "latest.json", json.dumps(metadata, ensure_ascii=False, indent=2))
                applied = True
            report["config"] = str(candidate)
            report["config_stat"] = _file_identity(str(candidate))
            try:
                paths.atomic_write_text(directory / "recent.json", json.dumps(report, ensure_ascii=False, indent=2))
            except OSError as exc:
                self._say(tt("无法保存最近测试结果：{0}", exc))
            self._say(tt("已验证配置：{0}；备份：{1}", candidate, backup))
            return TuningResult(str(candidate), baseline, best, best_threads, str(log_path))
        except TuningError as exc:
            self._log.append(str(exc))
            raise
        except (OSError, UnicodeError, ValueError) as exc:
            message = tt("调优失败：{0}", exc)
            self._log.append(message)
            raise TuningError(message) from exc
        finally:
            if candidate is not None and not applied:
                candidate.unlink(missing_ok=True)
            self._log.append(tt("总用时：{0:.1f} 秒", time.monotonic() - start))
            try:
                paths.atomic_write_text(log_path, "\n".join(self._log))
                self.message(tt("调优日志：{0}", log_path))
            except OSError as exc:
                self.message(tt("无法写入调优日志：{0}", exc))


    def restore(self) -> str:
        """Validate the most recent backup with a GTP search before the caller selects it."""
        self._deadline = time.monotonic() + TOTAL_TIMEOUT
        directory = (paths.app_home() / "tuning").resolve()
        try:
            metadata = json.loads((directory / "latest.json").read_text(encoding="utf-8"))
            backup = Path(metadata["backup"]).resolve()
            if (backup.parent != directory or not backup.name.startswith("backup-")
                    or not backup.is_file() or _sha(backup) != metadata["config_sha256"]):
                raise ValueError(tt("备份路径或内容无效"))
            _config_with_threads(backup.read_text(encoding="utf-8-sig"), 1)
            es = self.settings.engine
            for path in (es.executable, es.model):
                if not path or not Path(path).is_file():
                    raise ValueError(tt("引擎文件不可用：{0}", path))
            self._say(tt("正在用 GTP 和短搜索验证上次配置。"))
            self._self_check(str(Path(es.executable).resolve()), str(Path(es.model).resolve()), backup)
            self._check()
            self._say(tt("上次配置已通过验证：{0}", backup))
            return str(backup)
        except TuningError:
            raise
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise TuningError(tt("上次配置不可用：{0}", exc)) from exc
