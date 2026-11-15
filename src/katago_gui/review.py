"""Independent SGF replay and resumable KataGo analysis for game reviews."""
from __future__ import annotations

import hashlib
import json
import math
import re

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from . import paths, sgf
from .go.board import Board, IllegalMove
from .go.coords import Move, from_sgf, other, to_gtp
from .go.game import MoveRecord, SUPPORTED_SIZES
from .go.rules import get_rules, rules_from_sgf
from .i18n_review import review_tr as tr
from .settings import EngineSettings

DEFAULT_VISITS = 128
CACHE_VERSION = 1
ANALYSIS_OVERRIDE = "reportAnalysisWinratesAs=BLACK,nnMaxBatchSize=1"


@dataclass(frozen=True)
class ReviewPosition:
    board: Board
    to_move: str
    moves: tuple[MoveRecord, ...]
    analysis_initial_stones: tuple[tuple[str, str], ...]
    analysis_moves: tuple[tuple[str, str], ...]
    analysis_initial_player: str

    @property
    def last_move(self) -> Optional[MoveRecord]:
        return self.moves[-1] if self.moves else None


@dataclass
class ReviewRecord:
    size: int
    rules_key: str
    komi: float
    moves: list[MoveRecord]
    positions: list[ReviewPosition]
    source_text: str
    black_name: str = ""
    white_name: str = ""
    result: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def move_count(self) -> int:
        return len(self.moves)

    @property
    def finished(self) -> bool:
        return self.result not in ("", "?") or (
            len(self.moves) >= 2
            and self.moves[-1].move is None
            and self.moves[-2].move is None
        )

    def position(self, index: int) -> ReviewPosition:
        return self.positions[index]


@dataclass(frozen=True)
class CandidateEvaluation:
    move: str
    black_winrate: float
    black_score_lead: Optional[float]
    visits: int
    pv: tuple[str, ...] = ()


@dataclass(frozen=True)
class PositionEvaluation:
    black_winrate: Optional[float]
    black_score_lead: Optional[float]
    visits: int
    candidates: tuple[CandidateEvaluation, ...] = ()
    error: str = ""


@dataclass(frozen=True)
class MoveReview:
    number: int
    color: str
    move: Move
    winrate_change_black: Optional[float]
    winrate_change_view: Optional[float]
    score_change_black: Optional[float]
    winrate_loss: Optional[float]
    score_loss: Optional[float]
    played_candidate_known: bool
    best_move: Optional[str]


def _apply_setup(board: Board, node: Any, size: int) -> Board:
    grid = [list(row) for row in board.grid]
    occupied: dict[tuple[int, int], str] = {}
    for prop_name, color in (("AB", "B"), ("AW", "W"), ("AE", "")):
        prop = node.props.get(prop_name)
        if prop is None:
            continue
        for value in prop.values:
            point = from_sgf(value.strip(), size)
            if point is None:
                raise sgf.SgfError(tr("摆子属性不能使用停一手坐标"), prop.line, prop.col)
            if point in occupied:
                raise sgf.SgfError(tr("同一节点的摆子属性位置冲突"), prop.line, prop.col)
            occupied[point] = color
            grid[point[1]][point[0]] = color
    return Board(size, tuple(tuple(row) for row in grid), board.captures)


def _stones(board: Board) -> tuple[tuple[str, str], ...]:
    return tuple(
        (color, to_gtp((x, y), board.size))
        for y, row in enumerate(board.grid)
        for x, color in enumerate(row)
        if color
    )


def _position(
    board: Board,
    to_move: str,
    moves: list[MoveRecord],
    initial_stones: tuple[tuple[str, str], ...],
    analysis_moves: list[tuple[str, str]],
    initial_player: str,
) -> ReviewPosition:
    return ReviewPosition(
        board, to_move, tuple(moves), initial_stones, tuple(analysis_moves), initial_player
    )


def parse_review_sgf(text: str) -> ReviewRecord:
    """Parse and replay the main line, including setup stones and explicit player-to-move."""
    trees = sgf._Parser(text).collection()
    tree = trees[0]
    nodes: list[Any] = []
    current = tree
    skipped = 0
    while current is not None:
        nodes.extend(current.nodes)
        if len(current.children) > 1:
            skipped += len(current.children) - 1
        current = current.children[0] if current.children else None
    if not nodes:
        raise sgf.SgfError(tr("SGF 中没有节点"))
    root = nodes[0]

    def root_value(name: str, default: Optional[str] = None) -> Optional[str]:
        prop = root.props.get(name)
        return prop.values[0] if prop else default

    gm = root_value("GM")
    if gm is not None and gm.strip() != "1":
        raise sgf.SgfError(tr("不是围棋棋谱（GM 不为 1）"))
    ff = root_value("FF")
    if ff is not None and ff not in ("3", "4"):
        raise sgf.SgfError(tr("不支持 SGF 版本 FF[{0}]", ff))

    size_text = root_value("SZ", "19")
    if size_text is None or ":" in size_text:
        raise sgf.SgfError(tr("不支持的棋盘尺寸：SZ[{0}]", size_text))
    try:
        size = int(size_text)
    except ValueError:
        raise sgf.SgfError(tr("无效的棋盘尺寸：SZ[{0}]", size_text)) from None
    if size not in SUPPORTED_SIZES:
        raise sgf.SgfError(tr("不支持的棋盘尺寸 {0}（仅支持 9/13/19 路）", size))

    rules_key = "chinese"
    rules_text = root_value("RU")
    if rules_text:
        rules = rules_from_sgf(rules_text)
        if rules is not None:
            rules_key = rules.key
    rule_set = get_rules(rules_key)
    komi_text = root_value("KM")
    komi = rule_set.default_komi
    if komi_text is not None:
        try:
            komi = float(komi_text)
        except ValueError:
            raise sgf.SgfError(tr("无效的贴目：KM[{0}]", komi_text)) from None
        if not math.isfinite(komi) or abs(komi) > 150 or (komi * 2) % 1:
            raise sgf.SgfError(tr("贴目必须是 0.5 的整数倍：KM[{0}]", komi_text))

    for node in nodes:
        for name, prop in node.props.items():
            if name in {"B", "W", "PL", "HA"} and len(prop.values) != 1:
                raise sgf.SgfError(tr("属性 {0} 必须只有一个值", name), prop.line, prop.col)
            if node is not root and name in {
                "GM", "FF", "CA", "SZ", "KM", "RU", "PB", "PW", "DT", "RE", "AP"
            }:
                raise sgf.SgfError(tr("属性 {0} 只支持出现在根节点", name), prop.line, prop.col)
            if name == "HA" and prop.values[0].strip() not in ("", "0"):
                raise sgf.SgfError(tr("不支持让子局（HA 属性）"), prop.line, prop.col)

    root_pl = root_value("PL")
    if root_pl not in (None, "B", "W"):
        raise sgf.SgfError(tr("无效的先行方：PL[{0}]", root_pl))
    to_move = root_pl or "B"
    board = _apply_setup(Board(size), root, size)
    initial_stones = _stones(board)
    analysis_initial_player = to_move
    analysis_moves: list[tuple[str, str]] = []
    moves: list[MoveRecord] = []
    positions = [_position(
        board, to_move, moves, initial_stones, analysis_moves, analysis_initial_player
    )]
    warnings = []
    if len(trees) > 1:
        warnings.append(tr("文件包含 {0} 局棋谱，只读取第一局", len(trees)))
    if skipped:
        warnings.append(tr("忽略了 {0} 个变化分支，只读取主变化线", skipped))

    for node_index, node in enumerate(nodes):
        has_setup = node_index > 0 and any(name in node.props for name in ("AB", "AW", "AE"))
        if has_setup:
            board = _apply_setup(board, node, size)
            pl = node.props.get("PL")
            if pl:
                to_move = pl.values[0]
                if to_move not in ("B", "W"):
                    raise sgf.SgfError(tr("无效的先行方：PL[{0}]", to_move))
            elif "B" in node.props:
                to_move = "B"
            elif "W" in node.props:
                to_move = "W"
            initial_stones = _stones(board)
            analysis_initial_player = to_move
            analysis_moves = []
        elif node_index > 0 and "PL" in node.props:
            to_move = node.props["PL"].values[0]
            if to_move not in ("B", "W"):
                raise sgf.SgfError(tr("无效的先行方：PL[{0}]", to_move))
            initial_stones = _stones(board)
            analysis_initial_player = to_move
            analysis_moves = []
            if positions:
                positions[-1] = _position(
                    board, to_move, moves, initial_stones, analysis_moves,
                    analysis_initial_player
                )

        if has_setup and not ("B" in node.props or "W" in node.props):
            positions[-1] = _position(
                board, to_move, moves, initial_stones, analysis_moves,
                analysis_initial_player
            )
        has_black, has_white = "B" in node.props, "W" in node.props
        if has_black and has_white:
            raise sgf.SgfError(tr("同一节点同时包含黑白着手"), node.line, node.col)
        if not (has_black or has_white):
            continue
        color = "B" if has_black else "W"
        prop = node.props[color]
        if color != to_move:
            raise sgf.SgfError(
                tr("着手颜色不交替：第 {0} 手应为{1}方",
                   len(moves) + 1, tr("黑") if to_move == "B" else tr("白")),
                prop.line, prop.col,
            )
        try:
            move = from_sgf(prop.values[0].strip(), size)
            new_board = board.play(color, move, rule_set)
        except (ValueError, IllegalMove) as exc:
            raise sgf.SgfError(tr("第 {0} 手非法：{1}", len(moves) + 1, exc), prop.line, prop.col) from None
        if move is not None:
            if rule_set.ko == "SIMPLE" and len(positions) >= 2 and new_board == positions[-2].board:
                raise sgf.SgfError(tr("第 {0} 手非法：立即提回（劫）", len(moves) + 1), prop.line, prop.col)
            if rule_set.ko == "POSITIONAL" and any(
                new_board == position.board for position in positions
            ):
                raise sgf.SgfError(tr("第 {0} 手非法：全局同形", len(moves) + 1), prop.line, prop.col)
            if rule_set.ko == "SITUATIONAL" and any(
                new_board == position.board and position.to_move == other(color)
                for position in positions
            ):
                raise sgf.SgfError(tr("第 {0} 手非法：情境同形", len(moves) + 1), prop.line, prop.col)
        board = new_board
        moves.append(MoveRecord(color, move))
        analysis_moves.append((color, "pass" if move is None else to_gtp(move, size)))
        to_move = other(color)
        positions.append(_position(
            board, to_move, moves, initial_stones, analysis_moves, analysis_initial_player
        ))

    result = root_value("RE", "") or ""
    if result and not re.fullmatch(
        r"(?:0|Draw|Void|\?|[BW]\+(?:[0-9]+(?:\.[0-9]+)?|R|Resign|T|Time|F|Forfeit)?)",
        result.strip(),
    ):
        raise sgf.SgfError(tr("无效的终局结果 RE[{0}]", result))
    return ReviewRecord(
        size=size,
        rules_key=rules_key,
        komi=komi,
        moves=moves,
        positions=positions,
        source_text=text,
        black_name=root_value("PB", "") or "",
        white_name=root_value("PW", "") or "",
        result=result,
        warnings=warnings,
    )


def load_review_sgf(path: str | Path) -> ReviewRecord:
    return parse_review_sgf(sgf.decode_bytes(Path(path).read_bytes()))


def normalize_winrate(value: float, perspective: str, to_move: str = "B") -> float:
    """Convert a winrate fraction to black perspective."""
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(tr("胜率必须在 0 到 1 之间"))
    perspective = perspective.upper()
    if perspective == "BLACK":
        return value
    if perspective == "WHITE":
        return 1.0 - value
    if perspective in ("SIDETOMOVE", "SIDE_TO_MOVE"):
        return value if to_move == "B" else 1.0 - value
    raise ValueError(tr("未知胜率视角：{0}", perspective))


def parse_analysis_response(payload: dict[str, Any]) -> PositionEvaluation:
    root = payload.get("rootInfo") or {}
    current_player = str(root.get("currentPlayer", "B")).upper()
    perspective = str(payload.get("winratePerspective", "BLACK")).upper()
    root_wr = root.get("winrate")
    black_wr = normalize_winrate(root_wr, perspective, current_player) if root_wr is not None else None
    root_score = root.get("scoreLead")
    if root_score is not None:
        root_score = float(root_score)
        if not math.isfinite(root_score):
            raise ValueError(tr("目差不是有限数值"))
        if perspective == "WHITE" or (perspective in ("SIDETOMOVE", "SIDE_TO_MOVE") and current_player == "W"):
            root_score = -root_score
    candidates = []
    for item in payload.get("moveInfos", []):
        wr = normalize_winrate(item["winrate"], perspective, current_player)
        score = item.get("scoreLead")
        if score is not None:
            score = float(score)
            if not math.isfinite(score):
                raise ValueError(tr("候选目差不是有限数值"))
            if perspective == "WHITE" or (
                perspective in ("SIDETOMOVE", "SIDE_TO_MOVE") and current_player == "W"
            ):
                score = -score
        candidates.append(CandidateEvaluation(
            move=str(item["move"]),
            black_winrate=wr,
            black_score_lead=score,
            visits=max(0, int(item.get("visits", 0))),
            pv=tuple(str(v) for v in item.get("pv", ())),
        ))
    visits = max(0, int(root.get("visits", sum(item.visits for item in candidates))))
    return PositionEvaluation(black_wr, root_score, visits, tuple(candidates))


def position_request(
    record: ReviewRecord, position_index: int, request_id: str, visits: int = DEFAULT_VISITS
) -> dict[str, Any]:
    pos = record.position(position_index)
    return {
        "id": request_id,
        "moves": [list(move) for move in pos.analysis_moves],
        "initialStones": [list(stone) for stone in pos.analysis_initial_stones],
        "initialPlayer": pos.analysis_initial_player,
        "rules": record.rules_key,
        "komi": record.komi,
        "boardXSize": record.size,
        "boardYSize": record.size,
        "analyzeTurns": [len(pos.analysis_moves)],
        "maxVisits": int(visits),
        "includeOwnership": False,
    }


def cache_key(record: ReviewRecord, engine: EngineSettings, visits: int = DEFAULT_VISITS) -> str:
    def file_identity(value: str, hash_file: bool = False) -> dict[str, Any]:
        if not value:
            return {"path": ""}
        path = Path(value).expanduser().resolve()
        try:
            stat = path.stat()
            identity: dict[str, Any] = {
                "path": str(path).casefold(),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
            if hash_file and path.is_file():
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                identity["sha256"] = digest.hexdigest()
            return identity
        except OSError:
            return {"path": str(path).casefold(), "missing": True}

    identity = {
        "cache_version": CACHE_VERSION,
        "sgf_sha256": hashlib.sha256(record.source_text.encode("utf-8")).hexdigest(),
        "engine": file_identity(engine.executable),
        "model": file_identity(engine.model),
        "config": file_identity(engine.config, hash_file=True),
        "size": record.size,
        "rules": record.rules_key,
        "komi": record.komi,
        "visits": visits,
        "analysis_override": ANALYSIS_OVERRIDE,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _evaluation_to_json(evaluation: PositionEvaluation) -> dict[str, Any]:
    return {
        "black_winrate": evaluation.black_winrate,
        "black_score_lead": evaluation.black_score_lead,
        "visits": evaluation.visits,
        "candidates": [
            {
                "move": c.move, "black_winrate": c.black_winrate,
                "black_score_lead": c.black_score_lead, "visits": c.visits, "pv": list(c.pv),
            }
            for c in evaluation.candidates
        ],
    }


def _evaluation_from_json(raw: dict[str, Any]) -> PositionEvaluation:
    candidate_data = raw.get("candidates", [])
    payload = {
        "rootInfo": {
            "winrate": raw.get("black_winrate"),
            "scoreLead": raw.get("black_score_lead"),
            "visits": raw.get("visits", 0),
        },
        "moveInfos": [
            {
                "move": item["move"],
                "winrate": item["black_winrate"],
                "scoreLead": item.get("black_score_lead"),
                "visits": item.get("visits", 0),
                "pv": item.get("pv", []),
            }
            for item in candidate_data
        ],
        "winratePerspective": "BLACK",
    }
    return parse_analysis_response(payload)


class ReviewAnalysisTask(QObject):
    """Serial, cancellable KataGo JSON analysis; completed positions are cached immediately."""

    position_done = Signal(int, object)
    progress = Signal(int, int)
    failed = Signal(str)
    finished = Signal(bool)

    def __init__(
        self,
        engine: EngineSettings,
        visits: int = DEFAULT_VISITS,
        parent: Optional[QObject] = None,
        command_builder: Optional[Callable[[EngineSettings], tuple[str, list[str]]]] = None,
        start_guard: Optional[Callable[[], bool]] = None,
    ):
        super().__init__(parent)
        self.engine_settings = engine
        self.visits = visits
        self.command_builder = command_builder
        self.start_guard = start_guard
        self.process: Optional[QProcess] = None
        self._record: Optional[ReviewRecord] = None
        self._evaluations: list[Optional[PositionEvaluation]] = []
        self._pending: list[int] = []
        self._current_index: Optional[int] = None
        self._request_id = 0
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._cache_path: Optional[Path] = None
        self._cache_doc: dict[str, Any] = {}
        self._active = False
        self._stopping = False
        self._cancel_requested = False
        self._finished_emitted = False
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setSingleShot(True)
        self._shutdown_timer.timeout.connect(self._kill_after_shutdown)

    @property
    def is_running(self) -> bool:
        return self._active

    @property
    def evaluations(self) -> list[Optional[PositionEvaluation]]:
        return list(self._evaluations)

    def start(
        self,
        record: ReviewRecord,
        cache_file: Optional[Path] = None,
        cache_identity: Optional[str] = None,
        start_guard: Optional[Callable[[], bool]] = None,
    ) -> None:
        if self._active:
            return
        guard = start_guard if start_guard is not None else self.start_guard
        if guard is not None and not guard():
            return
        self._record = record
        self._active = True
        self._stopping = False
        self._cancel_requested = False
        self._finished_emitted = False
        count = len(record.positions)
        self._evaluations = [None] * count
        self._cache_path = cache_file or (
            paths.app_home() / "review_cache" / f"{cache_identity or cache_key(record, self.engine_settings, self.visits)}.json"
        )
        self._cache_doc = {"version": CACHE_VERSION, "positions": {}}
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
            if raw.get("version") == CACHE_VERSION and isinstance(raw.get("positions"), dict):
                self._cache_doc = raw
        except (OSError, ValueError, AttributeError):
            pass
        for key, raw in self._cache_doc["positions"].items():
            try:
                index = int(key)
                if 0 <= index < count:
                    self._evaluations[index] = _evaluation_from_json(raw)
                    self.position_done.emit(index, self._evaluations[index])
            except (TypeError, ValueError, KeyError):
                continue
        self._pending = [i for i, evaluation in enumerate(self._evaluations) if evaluation is None]
        self.progress.emit(count - len(self._pending), count)
        if not self._pending:
            self._complete(True)
            return
        try:
            program, args = self._command()
        except (OSError, ValueError) as exc:
            self.failed.emit(str(exc))
            self._complete(False)
            return
        proc = QProcess(self)
        proc.setProgram(program)
        proc.setArguments(args)
        exe_path = Path(self.engine_settings.executable)
        if exe_path.is_file():
            proc.setWorkingDirectory(str(exe_path.resolve().parent))
        proc.readyReadStandardOutput.connect(self._read_stdout)
        proc.readyReadStandardError.connect(self._read_stderr)
        proc.errorOccurred.connect(self._process_error)
        proc.finished.connect(self._process_finished)
        proc.started.connect(self._send_next)
        self.process = proc
        proc.start()

    def _command(self) -> tuple[str, list[str]]:
        if self.command_builder:
            return self.command_builder(self.engine_settings)
        missing = self.engine_settings.missing()
        if missing:
            raise ValueError("；".join(missing))
        return self.engine_settings.executable, [
            "analysis", "-model", self.engine_settings.model, "-config", self.engine_settings.config,
            "-analysis-threads", "1",
            "-override-config", ANALYSIS_OVERRIDE,
        ]

    def _read_stdout(self) -> None:
        if self.process is None:
            return
        self._stdout.extend(bytes(self.process.readAllStandardOutput()))
        while b"\n" in self._stdout:
            raw_line, _, remainder = self._stdout.partition(b"\n")
            self._stdout = bytearray(remainder)
            self._handle_line(raw_line.decode("utf-8", errors="replace").strip())

    def _handle_line(self, line: str) -> None:
        if not line.startswith("{") or self._current_index is None or self._record is None:
            return
        try:
            payload = json.loads(line)
            if str(payload.get("id")) != str(self._request_id):
                return
            if payload.get("error"):
                raise ValueError(str(payload["error"]))
            evaluation = parse_analysis_response(payload)
        except (json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
            self.failed.emit(tr("KataGo 分析响应无效：{0}", exc))
            self.cancel()
            return
        index = self._current_index
        self._current_index = None
        self._evaluations[index] = evaluation
        self._cache_doc["positions"][str(index)] = _evaluation_to_json(evaluation)
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            paths.atomic_write_text(
                self._cache_path,
                json.dumps(self._cache_doc, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
            )
        except (OSError, ValueError) as exc:
            self.failed.emit(tr("分析缓存写入失败，当前结果仍保留：{0}", exc))
        self.position_done.emit(index, evaluation)
        self.progress.emit(sum(item is not None for item in self._evaluations), len(self._evaluations))
        self._send_next()

    def _send_next(self) -> None:
        if not self._active or self._stopping or self.process is None:
            return
        if not self._pending:
            self._stopping = True
            self.process.closeWriteChannel()
            self._shutdown_timer.start(3000)
            return
        self._current_index = self._pending.pop(0)
        self._request_id += 1
        request = position_request(
            self._record, self._current_index, str(self._request_id), self.visits
        )
        self.process.write((json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8"))

    def _read_stderr(self) -> None:
        if self.process is not None:
            self._stderr.extend(bytes(self.process.readAllStandardError()))
            if len(self._stderr) > 16_384:
                del self._stderr[:-16_384]

    def _process_error(self, _error: QProcess.ProcessError) -> None:
        if self._active and not self._stopping:
            detail = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace") if self.process else ""
            self.failed.emit(tr("KataGo 分析进程错误：{0}", detail.strip() or "无法启动"))
            self._complete(False)

    def _process_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        if self._cancel_requested:
            self._shutdown_timer.stop()
            proc, self.process = self.process, None
            if proc is not None:
                proc.deleteLater()
            self._complete(False)
            return
        self._shutdown_timer.stop()
        proc, self.process = self.process, None
        if proc is not None:
            proc.deleteLater()
        if self._active and not self._finished_emitted:
            if not self._stopping and (exit_code != 0 or self._pending or self._current_index is not None):
                detail = self._stderr.decode("utf-8", errors="replace").strip()
                self.failed.emit(tr("KataGo 分析进程意外结束：{0}", detail or exit_code))
                self._complete(False)
            else:
                self._complete(True)

    def cancel(self) -> None:
        if not self._active:
            return
        self._cancel_requested = True
        self._stopping = True
        self._pending.clear()
        self._current_index = None
        self._shutdown_timer.stop()
        proc, self.process = self.process, None
        if proc is not None:
            proc.kill()
            if not proc.waitForFinished(1000):
                self.process = proc
                return
            proc.deleteLater()
        self._complete(False)

    def wait_for_finished(self, timeout_ms: int = 5000) -> bool:
        if not self._active:
            return True
        self.cancel()
        proc = self.process
        if self._active and proc is not None:
            proc.waitForFinished(max(0, timeout_ms))
            if not self._active and self.process is proc:
                self.process = None
                proc.deleteLater()
            elif self._active and proc.state() == QProcess.ProcessState.NotRunning:
                self.process = None
                proc.deleteLater()
                self._complete(False)
        return not self._active

    def _kill_after_shutdown(self) -> None:
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()

    def _complete(self, success: bool) -> None:
        if self._finished_emitted:
            return
        self._active = False
        self._finished_emitted = True
        if success:
            self.progress.emit(
                sum(item is not None for item in self._evaluations), len(self._evaluations)
            )
        self.finished.emit(success)


def move_reviews(
    record: ReviewRecord,
    evaluations: list[Optional[PositionEvaluation]],
    human_color: str,
) -> list[MoveReview]:
    reviews: list[MoveReview] = []
    for index, played in enumerate(record.moves):
        before = evaluations[index] if index < len(evaluations) else None
        after = evaluations[index + 1] if index + 1 < len(evaluations) else None
        delta = (
            after.black_winrate - before.black_winrate
            if before and after and before.black_winrate is not None and after.black_winrate is not None
            else None
        )
        score_delta = (
            after.black_score_lead - before.black_score_lead
            if before and after and before.black_score_lead is not None and after.black_score_lead is not None
            else None
        )
        candidates = before.candidates if before else ()
        best = max(
            candidates,
            key=lambda candidate: candidate.black_winrate if played.color == "B" else 1 - candidate.black_winrate,
            default=None,
        )
        actual = next(
            (candidate for candidate in candidates if candidate.move.lower() == (
                "pass" if played.move is None else to_gtp(played.move, record.size).lower()
            )),
            None,
        )
        loss = None
        score_loss = None
        if best is not None and actual is not None:
            best_side = best.black_winrate if played.color == "B" else 1 - best.black_winrate
            actual_side = actual.black_winrate if played.color == "B" else 1 - actual.black_winrate
            loss = max(0.0, best_side - actual_side)
            if best.black_score_lead is not None and actual.black_score_lead is not None:
                best_score = best.black_score_lead if played.color == "B" else -best.black_score_lead
                actual_score = actual.black_score_lead if played.color == "B" else -actual.black_score_lead
                score_loss = max(0.0, best_score - actual_score)
        view_delta = delta if human_color == "B" else (-delta if delta is not None else None)
        reviews.append(MoveReview(
            number=index + 1, color=played.color, move=played.move,
            winrate_change_black=delta, winrate_change_view=view_delta,
            score_change_black=score_delta, winrate_loss=loss, score_loss=score_loss,
            played_candidate_known=actual is not None,
            best_move=best.move if best is not None else None,
        ))
    return reviews
