from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

import ast
import string

from katago_gui import i18n_tuning, tuning
from katago_gui.i18n import set_language
from katago_gui.engine.launch import OVERRIDES
from katago_gui.settings import EngineSettings, Settings
from katago_gui.ui.tuning_panel import TuningPanel


VERSION = "KataGo v1.18.1\nUsing Eigen(CPU) backend\n"
HELP = "benchmark : Test speed\n"
BENCH_HELP = " ".join(("-config", "-model", "-v", "-t", "-n", "-boardsize", "-override-config",
                       "-no-server-thread-test", "-no-half-batch-size-test"))
CONFIG = "rules = japanese\nmaxVisits = 500\nnumSearchThreads = 4 # current\nkomi = 6.5\n"


@pytest.fixture
def settings(tmp_path):
    engine = tmp_path / "engine.exe"
    model = tmp_path / "model.bin.gz"
    config = tmp_path / "gtp.cfg"
    engine.write_bytes(b"engine")
    model.write_bytes(b"model")
    config.write_text(CONFIG, encoding="utf-8")
    return Settings(engine=EngineSettings(str(engine), str(model), str(config)))


class FakeProcess:
    calls = []
    speeds = {2: 100, 4: 80, 8: 70}
    fail_gtp = False
    version = VERSION
    help = HELP
    benchmark_help = BENCH_HELP

    def __init__(self, args, **kwargs):
        assert isinstance(args, list)
        assert kwargs["cwd"] == str(tuning.paths.engine_work_dir())
        self.args = args
        self.returncode = None
        self.killed = False
        self.calls.append(args)

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def communicate(self, input=None, timeout=None):
        cmd = self.args[1]
        self.returncode = 0
        if cmd == "version":
            output = self.version
        elif cmd == "help":
            output = self.help
        elif "-help" in self.args:
            output = self.benchmark_help
        elif cmd == "benchmark":
            threads = int(self.args[self.args.index("-t") + 1])
            output = f"numSearchThreads = {threads}: 1 / 1 positions, visits/s = {self.speeds[threads]} nnEvals/s = 20\n"
        else:
            commands = input.splitlines()
            output = "".join(f"{'?' if self.fail_gtp and i == 8 else '='}{i} "
                             f"{'D4' if i == 9 else ''}\n\n"
                             for i in range(1, len(commands) + 1))
        return output, ""


@pytest.fixture
def fake(monkeypatch):
    FakeProcess.calls = []
    FakeProcess.speeds = {2: 100, 4: 80, 8: 70}
    FakeProcess.fail_gtp = False
    FakeProcess.version = VERSION
    FakeProcess.help = HELP
    FakeProcess.benchmark_help = BENCH_HELP
    monkeypatch.setattr(tuning.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(tuning.os, "cpu_count", lambda: 8)
    return FakeProcess


def test_improvement_verified_and_original_preserved(settings, fake):
    messages = []
    result = tuning.Tuner(settings, messages.append).run()
    assert result.config
    assert Path(settings.engine.config).read_text(encoding="utf-8") == CONFIG
    assert Path(result.config).read_text(encoding="utf-8") == CONFIG.replace("= 4 #", "= 2 #")
    settings.engine.config = result.config
    assert Path(tuning.Tuner(settings).restore()).read_text(encoding="utf-8") == CONFIG
    assert (result.baseline, result.best, result.threads) == (80, 100, 2)
    benchmarks = [x for x in fake.calls if x[1] == "benchmark" and "-help" not in x]
    assert len(benchmarks) == 9
    assert all(x[x.index("-override-config") + 1] == OVERRIDES for x in benchmarks)
    gtp = [x for x in fake.calls if x[1] == "gtp"][0]
    assert gtp[gtp.index("-override-config") + 1] == OVERRIDES
    assert "调优前中位数" in "\n".join(messages)
    assert Path(result.log).is_file()


def test_no_gain_keeps_config(settings, fake):
    fake.speeds = {2: 82, 4: 80, 8: 79}
    messages = []
    result = tuning.Tuner(settings, messages.append).run()
    assert result.config is None
    assert "没有显著收益" in "\n".join(messages)
    assert not any(c[1] == "gtp" for c in fake.calls)
    assert Path(settings.engine.config).read_text(encoding="utf-8") == CONFIG


def test_failed_gtp_never_applies(settings, fake, isolated_home):
    fake.fail_gtp = True
    with pytest.raises(tuning.TuningError, match="自检"):
        tuning.Tuner(settings).run()
    assert not list((isolated_home / "tuning").glob("config-*.cfg"))
    assert not (isolated_home / "tuning" / "latest.json").exists()
    assert Path(settings.engine.config).read_text(encoding="utf-8") == CONFIG


def test_changed_config_is_not_applied(settings, fake, monkeypatch):
    class Changing(FakeProcess):
        def communicate(self, input=None, timeout=None):
            output = super().communicate(input, timeout)
            if self.args[1] == "gtp":
                Path(settings.engine.config).write_text(CONFIG + "# edited\n", encoding="utf-8")
            return output

    monkeypatch.setattr(tuning.subprocess, "Popen", Changing)
    with pytest.raises(tuning.TuningError, match="调优期间"):
        tuning.Tuner(settings).run()
    assert Path(settings.engine.config).read_text(encoding="utf-8") == CONFIG + "# edited\n"


def test_recent_result_detects_changed_model(settings, fake):
    result = tuning.Tuner(settings).run()
    settings.engine.config = result.config
    assert "文件和环境标识一致" in tuning.recent_result(settings)
    Path(settings.engine.model).write_bytes(b"changed model")
    assert "已过期" in tuning.recent_result(settings)


@pytest.mark.parametrize("attribute,value,error", [
    ("version", "unknown", "后端"),
    ("help", "gtp", "benchmark"),
    ("benchmark_help", "-t", "选项"),
])
def test_unsupported_capabilities(settings, fake, attribute, value, error):
    setattr(fake, attribute, value)
    with pytest.raises(tuning.TuningError, match=error):
        tuning.Tuner(settings).run()


def test_cancel_kills_active_process(settings, monkeypatch):
    entered = threading.Event()

    class Hanging(FakeProcess):
        instance = None

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            Hanging.instance = self

        def communicate(self, input=None, timeout=None):
            entered.set()
            if self.killed:
                return "", ""
            raise subprocess.TimeoutExpired(self.args, timeout)

    monkeypatch.setattr(tuning.subprocess, "Popen", Hanging)
    tuner = tuning.Tuner(settings)
    errors = []

    def run():
        try:
            tuner.run()
        except tuning.TuningError as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    assert entered.wait(5)
    tuner.cancel()
    thread.join(5)
    assert not thread.is_alive()
    assert Hanging.instance.killed
    assert isinstance(errors[0], tuning.TuningCancelled)
    assert Path(settings.engine.config).read_text(encoding="utf-8") == CONFIG


def test_timeout_kills_process(settings, monkeypatch):
    class Hanging(FakeProcess):
        instance = None

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            Hanging.instance = self

        def communicate(self, input=None, timeout=None):
            if self.killed:
                return "", ""
            raise subprocess.TimeoutExpired(self.args, timeout)

    monkeypatch.setattr(tuning.subprocess, "Popen", Hanging)
    monkeypatch.setattr(tuning, "COMMAND_TIMEOUT", 0.001)
    with pytest.raises(tuning.TuningError, match="超时"):
        tuning.Tuner(settings).run()
    assert Hanging.instance.killed


def test_failure_classification_and_bad_benchmark():
    assert "内存不足" in tuning._failure(1, "CUDA out of memory")
    assert "DLL" in tuning._failure(-1073741515, "")
    assert "设备不可用" in tuning._failure(1, "no GPU")
    with pytest.raises(tuning.TuningError):
        tuning._benchmark_value("numSearchThreads = 4: 0 / 1 positions, visits/s = 0", 4)


def test_panel_signals_and_validated_restore(settings, fake, qtbot):
    panel = TuningPanel(settings)
    qtbot.addWidget(panel)
    applied = []
    states = []
    panel.config_applied.connect(lambda path: applied.append((path, panel.is_running)))
    panel.running_changed.connect(states.append)
    panel.start()
    assert panel.is_running
    assert panel.wait_for_finished(10000)
    assert states == [True, False]
    assert len(applied) == 1 and applied[0][1]
    calls = len(fake.calls)
    panel.start_guard = lambda: False
    panel.restore()
    assert len(fake.calls) == calls and not panel.is_running
    panel.start_guard = lambda: True
    panel.restore()
    assert panel.wait_for_finished(10000)
    assert Path(applied[-1][0]).read_text(encoding="utf-8") == CONFIG
    assert fake.calls[-1][1] == "gtp"
    assert states == [True, False, True, False]


def test_restore_rejects_tampered_backup(settings, fake, isolated_home):
    tuning.Tuner(settings).run()
    backup = next((isolated_home / "tuning").glob("backup-*.cfg"))
    backup.write_text(CONFIG + "# changed\n", encoding="utf-8")
    calls = len(fake.calls)
    with pytest.raises(tuning.TuningError, match="上次配置不可用"):
        tuning.Tuner(settings).restore()
    assert len(fake.calls) == calls


def test_panel_guard_prevents_worker_and_process(settings, fake, qtbot):
    panel = TuningPanel(settings)
    qtbot.addWidget(panel)
    states = []
    panel.running_changed.connect(states.append)
    panel.start_guard = lambda: False
    panel.start()
    assert not panel.is_running
    assert not states
    assert not fake.calls
    assert panel.wait_for_finished(0)


def test_panel_cancel_wait_does_not_apply(settings, qtbot, monkeypatch):
    class Hanging(FakeProcess):
        def communicate(self, input=None, timeout=None):
            if self.killed:
                return "", ""
            raise subprocess.TimeoutExpired(self.args, timeout)

    monkeypatch.setattr(tuning.subprocess, "Popen", Hanging)
    panel = TuningPanel(settings)
    qtbot.addWidget(panel)
    applied, states = [], []
    panel.config_applied.connect(applied.append)
    panel.running_changed.connect(states.append)
    panel.start()
    panel.cancel()
    assert panel.wait_for_finished(5000)
    assert not panel.is_running and states == [True, False] and not applied


def test_tuning_catalog_covers_literals_and_formats():
    formatter = string.Formatter()
    root = Path(tuning.__file__).parent
    for path in (root / "tuning.py", root / "ui" / "tuning_panel.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "tt":
                assert node.args[0].value in i18n_tuning.ENGLISH, (path.name, node.lineno)
    for source, english in i18n_tuning.ENGLISH.items():
        assert not any("\u4e00" <= ch <= "\u9fff" for ch in english), source
        fields = lambda text: {(f, spec, conv) for _, f, spec, conv in formatter.parse(text) if f is not None}
        assert fields(source) == fields(english)


def test_runtime_messages_follow_language(settings, fake, qtbot):
    set_language("en")
    messages = []
    tuning.Tuner(settings, messages.append).run()
    assert "Checking engine version" in "\n".join(messages)
    assert not any("\u4e00" <= ch <= "\u9fff" for line in messages for ch in line.split(":", 1)[0])
    panel = TuningPanel(settings)
    qtbot.addWidget(panel)
    assert panel.start_button.text() == "Start tuning"
    assert panel.recent.text().startswith("Last measurement")
