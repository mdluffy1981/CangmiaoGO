from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLayout,
                               QListWidget, QListWidgetItem, QPushButton, QScrollArea,
                               QSplitter, QVBoxLayout, QWidget)

from ..go.board import Board, IllegalMove
from ..go.coords import other, to_gtp
from ..go.game import MoveRecord
from ..go.rules import get_rules
from ..i18n import tr
from ..paths import app_home, atomic_write_text
from ..sgf import SgfError
from ..tsumego import (TsumegoNode, TsumegoProblem, default_tsumego, load_tsumego,
                       localize_default_tsumego)
from .board_widget import BoardWidget


@dataclass
class PuzzlePosition:
    size: int
    board: Board
    to_move: str
    moves: list[MoveRecord] = field(default_factory=list)
    last_move: Optional[MoveRecord] = None
    boards: list[Board] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.boards:
            self.boards.append(self.board)


def _has_correct(node: TsumegoNode) -> bool:
    if node.verdict == "wrong":
        return False
    if not node.children:
        return node.verdict == "correct"
    return any(_has_correct(child) for child in node.children)


class TsumegoPage(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.problems: list[TsumegoProblem] = []
        self.source_path: Optional[Path] = None
        self.collection_id = ""
        self.problem_index = 0
        self.cursor: Optional[TsumegoNode] = None
        self.position = PuzzlePosition(19, Board(19), "B")
        self.progress: dict[str, str] = {}
        self.records: dict[str, dict] = {}
        self.legacy: dict[str, str] = {}
        self.collections: list[str] = []
        self.last_collection = ""
        self.last_problem = ""
        self.last_by_collection: dict[str, str] = {}
        self._progress_error = ""
        self._progress_writable = True
        self.progress_path = app_home() / "tsumego_progress.json"
        self._load_progress()
        self._assisted = False
        self._solution_used = False
        self._finished = False
        self._full_board = False

        self.board = BoardWidget()
        self.board.point_clicked.connect(self._play_point)
        self.title_label = QLabel()
        self.instructions = QLabel()
        self.instructions.setWordWrap(True)
        self.collection_combo = QComboBox()
        self.collection_combo.currentIndexChanged.connect(self._switch_collection)
        self.filter_combo = QComboBox()
        self.filter_combo.currentIndexChanged.connect(self._refresh_list)
        self.difficulty_combo = QComboBox()
        self.difficulty_combo.currentIndexChanged.connect(self._refresh_list)
        self.source_label = QLabel()
        self.source_label.setWordWrap(True)
        self.problem_label = QLabel()
        self.problem_label.setWordWrap(True)
        self.problem_list = QListWidget()
        self.problem_list.currentRowChanged.connect(self._select_problem)
        self.status_label = QLabel(self._progress_error)
        self._status_data: tuple[str, tuple] | None = None
        self.status_label.setWordWrap(True)

        self.import_button = QPushButton()
        self.import_button.clicked.connect(self._open_file)
        self.previous_button = QPushButton()
        self.previous_button.clicked.connect(lambda: self._step_problem(-1))
        self.next_button = QPushButton()
        self.next_button.clicked.connect(lambda: self._step_problem(1))
        self.random_button = QPushButton()
        self.random_button.clicked.connect(self._random_problem)
        self.reset_button = QPushButton()
        self.reset_button.clicked.connect(self._reset)
        self.hint_button = QPushButton()
        self.hint_button.clicked.connect(self._hint)
        self.answer_button = QPushButton()
        self.answer_button.clicked.connect(self._show_answer)
        self.view_button = QPushButton()
        self.view_button.clicked.connect(self._toggle_view)

        side = QWidget()
        side.setMinimumWidth(240)
        side_layout = QVBoxLayout(side)
        for widget in (self.title_label, self.instructions, self.collection_combo,
                       self.import_button, self.source_label, self.filter_combo,
                       self.difficulty_combo):
            side_layout.addWidget(widget)
        side_layout.addWidget(self.problem_list, 1)
        side_layout.addWidget(self.problem_label)
        for button in (self.previous_button, self.next_button, self.random_button,
                       self.reset_button, self.hint_button, self.answer_button, self.view_button):
            side_layout.addWidget(button)
        side_layout.addWidget(self.status_label)
        self.side_scroll = QScrollArea()
        self.side_scroll.setWidget(side)
        self.side_scroll.setWidgetResizable(True)
        self.side_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.split = QSplitter()
        self.split.addWidget(self.side_scroll)
        self.split.addWidget(self.board)
        self.split.setStretchFactor(1, 3)
        self.split.setCollapsible(0, False)
        self.split.setCollapsible(1, False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        layout.addWidget(self.split)
        for combo in (self.collection_combo, self.filter_combo, self.difficulty_combo):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
        self.retranslate_ui()
        self._populate_collections()
        if self.last_collection and self.last_collection in self.collections:
            try:
                self.load_problems(self.last_collection)
            except (OSError, ValueError, SgfError):
                self._use_defaults()
        else:
            self._use_defaults()
        if self._progress_error:
            self.status_label.setText(self._progress_error)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_layout(event.size().width(), event.size().height())

    def minimumSizeHint(self) -> QSize:
        # Narrow pages switch to a stacked layout instead of forcing side and board widths together.
        board = self.board.minimumSize()
        return QSize(max(board.width(), self._side_width()), board.height() + 160)

    def _side_width(self) -> int:
        side = self.side_scroll.widget()
        scrollbar = self.side_scroll.verticalScrollBar().sizeHint().width()
        return max(side.minimumWidth(), side.minimumSizeHint().width()) + scrollbar + 2 * self.side_scroll.frameWidth()

    def _update_layout(self, width: int, height: int) -> None:
        side_width = self._side_width()
        narrow = width < side_width + self.board.minimumWidth() + self.split.handleWidth()
        self.side_scroll.setMinimumWidth(0 if narrow else side_width)
        orientation = Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal
        if self.split.orientation() == orientation:
            return
        self.split.setOrientation(orientation)
        if narrow:
            self.split.insertWidget(0, self.board)
            self.split.setSizes([max(self.board.minimumHeight(), height * 3 // 5), height * 2 // 5])
        else:
            self.split.insertWidget(0, self.side_scroll)

    def _status(self, source: str, *args: object) -> None:
        self._status_data = (source, args)
        self.status_label.setText(tr(source, *(tr(arg) if isinstance(arg, str) else arg for arg in args)))

    def _load_progress(self) -> None:
        try:
            data = json.loads(self.progress_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            self._progress_error = tr("无法读取做题进度：{0}", e)
            self._progress_writable = False
            return
        if not isinstance(data, dict):
            self._progress_error = tr("做题进度文件格式无效")
            self._progress_writable = False
            return
        if data.get("version") == 2:
            progress = data.get("progress", {})
            records = data.get("records", {})
            collections = data.get("collections", [])
            if not isinstance(progress, dict) or not isinstance(records, dict) or not isinstance(collections, list):
                self._progress_error = tr("做题进度文件格式无效")
                self._progress_writable = False
                return
            self.progress = {k: v for k, v in progress.items()
                             if isinstance(k, str) and v in ("correct", "wrong", "unknown", "viewed")}
            self.records = {k: v for k, v in records.items()
                            if isinstance(k, str) and isinstance(v, dict)}
            self.legacy = data.get("legacy", {}) if isinstance(data.get("legacy"), dict) else {}
            self.collections = [p for p in collections if isinstance(p, str)]
            self.last_collection = data.get("last_collection", "") if isinstance(data.get("last_collection", ""), str) else ""
            self.last_problem = data.get("last_problem", "") if isinstance(data.get("last_problem", ""), str) else ""
            last_by_collection = data.get("last_by_collection", {})
            if isinstance(last_by_collection, dict):
                self.last_by_collection = {k: v for k, v in last_by_collection.items()
                                           if isinstance(k, str) and isinstance(v, str)}
        elif all(isinstance(k, str) and v in ("correct", "wrong") for k, v in data.items()):
            self.legacy = data
        else:
            self._progress_error = tr("做题进度文件格式无效")
            self._progress_writable = False

    def _save_progress(self) -> None:
        if not self._progress_writable:
            return
        data = {"version": 2, "progress": self.progress, "records": self.records,
                "legacy": self.legacy, "collections": self.collections,
                "last_collection": self.last_collection, "last_problem": self.last_problem,
                "last_by_collection": self.last_by_collection}
        try:
            atomic_write_text(self.progress_path, json.dumps(data, ensure_ascii=False, indent=2))
        except OSError as e:
            self._status("保存做题进度失败：{0}", e)

    def _populate_collections(self) -> None:
        self.collection_combo.blockSignals(True)
        self.collection_combo.clear()
        self.collection_combo.addItem(tr("自制示例题集"), "")
        for path in self.collections:
            self.collection_combo.addItem(Path(path).name, path)
        self.collection_combo.blockSignals(False)

    def _switch_collection(self, index: int) -> None:
        path = self.collection_combo.itemData(index)
        if not path:
            self._use_defaults()
            return
        try:
            self.load_problems(path)
        except (OSError, ValueError, SgfError) as e:
            self._status("无法导入题集：{0}", e)

    def _use_defaults(self) -> None:
        self.source_path = None
        self.problems = default_tsumego()
        self._activate_collection()

    def load_problems(self, path: str | Path) -> None:
        source = Path(path).resolve()
        problems = load_tsumego(source)
        if not problems:
            raise ValueError(tr("题集为空"))
        self.source_path = source
        self.problems = problems
        path_string = str(source)
        if path_string not in self.collections:
            self.collections.append(path_string)
        for index, problem in enumerate(problems):
            old_key = f"{source}#{index}"
            if old_key in self.legacy and problem.problem_id not in self.progress:
                # Legacy "correct" included unmarked branches and assisted attempts.
                self.progress[problem.problem_id] = (
                    "wrong" if self.legacy[old_key] == "wrong" else "unknown")
                self.records.setdefault(problem.problem_id, {})["legacy_result"] = self.legacy[old_key]
        self._populate_collections()
        self._activate_collection()

    def _activate_collection(self, resume: bool = True) -> None:
        path = str(self.source_path) if self.source_path else ""
        content = "\n".join(sorted(problem.problem_id for problem in self.problems))
        self.collection_id = hashlib.sha256(content.encode("ascii")).hexdigest()
        self.collection_combo.blockSignals(True)
        self.collection_combo.setCurrentIndex(max(0, self.collection_combo.findData(path)))
        self.collection_combo.blockSignals(False)
        self.problem_index = 0
        if resume:
            previous = self.last_by_collection.get(
                self.collection_id, self.last_by_collection.get(
                    path, self.last_problem if self.last_collection == path else ""))
            self.problem_index = next((i for i, p in enumerate(self.problems)
                                       if p.problem_id == previous), 0)
        self._populate_difficulties(0)
        self.last_collection = path
        self._load_problem()

    def _populate_difficulties(self, index: int) -> None:
        self.difficulty_combo.blockSignals(True)
        self.difficulty_combo.clear()
        self.difficulty_combo.addItem(tr("全部难度"), "")
        for difficulty in dict.fromkeys(p.difficulty for p in self.problems if p.difficulty):
            self.difficulty_combo.addItem(difficulty, difficulty)
        self.difficulty_combo.setCurrentIndex(index if 0 <= index < self.difficulty_combo.count() else 0)
        self.difficulty_combo.blockSignals(False)

    def _open_file(self) -> None:
        start = str(self.source_path.parent if self.source_path else Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, tr("导入 SGF 题集"), start, tr("SGF 棋谱 (*.sgf);;所有文件 (*)"))
        if path:
            try:
                self.load_problems(path)
            except (OSError, ValueError, SgfError) as e:
                self._status("无法导入题集：{0}", e)

    def _select_problem(self, row: int) -> None:
        item = self.problem_list.item(row)
        if item is not None:
            index = item.data(Qt.ItemDataRole.UserRole)
            if index != self.problem_index:
                self.problem_index = index
                self._load_problem()

    def _problem_key(self) -> str:
        return self.problems[self.problem_index].problem_id if self.problems else ""

    def _load_problem(self) -> None:
        problem = self.problems[self.problem_index]
        self.cursor = problem.root
        self.position = PuzzlePosition(problem.size, problem.board, problem.to_move)
        self._assisted = False
        self._solution_used = False
        self._finished = False
        self.last_problem = problem.problem_id
        self.last_by_collection[self.collection_id] = problem.problem_id
        self.board.set_preview([])
        self._update_problem_label()
        status = self.progress.get(self._problem_key())
        labels = {"correct": "正解", "wrong": "错误", "unknown": "答案未知",
                  "viewed": "已查看答案"}
        if status:
            self._status("上次结果：{0}", labels[status])
        else:
            self._status("轮到{0}方行棋", "黑" if problem.to_move == "B" else "白")
        self._save_progress()
        self._render()

    def _update_problem_label(self) -> None:
        problem = self.problems[self.problem_index]
        self.problem_label.setText(tr("{0} · {1} 先行\n目标：{2}\n来源：{3} · 许可：{4}",
                                      problem.title, tr("黑" if problem.to_move == "B" else "白"),
                                      problem.goal or tr("未注明"), problem.source or tr("未注明"),
                                      problem.license or tr("未注明")) +
                                   ("\n" + tr("劫题暂不支持可靠判定") if not problem.supported else ""))

    def _refresh_list(self, *_args) -> None:
        if not self.problems:
            return
        self.problem_list.blockSignals(True)
        self.problem_list.clear()
        selected = -1
        filter_key = self.filter_combo.currentData()
        difficulty = self.difficulty_combo.currentData()
        labels = {"correct": tr("正解"), "wrong": tr("错误"), "unknown": tr("未知"),
                  "viewed": tr("已查看答案")}
        for i, problem in enumerate(self.problems):
            status = self.progress.get(problem.problem_id)
            if filter_key == "wrong" and status != "wrong":
                continue
            if filter_key == "uncompleted" and status == "correct":
                continue
            if difficulty and problem.difficulty != difficulty:
                continue
            item = QListWidgetItem(f"{problem.title} · {labels.get(status, tr('未完成'))}")
            item.setData(Qt.ItemDataRole.UserRole, i)
            self.problem_list.addItem(item)
            if i == self.problem_index:
                selected = self.problem_list.count() - 1
        self.problem_list.setCurrentRow(selected)
        self.problem_list.blockSignals(False)
        completed = sum(self.progress.get(p.problem_id) == "correct" for p in self.problems)
        name = self.source_path.name if self.source_path else tr("自制示例题集")
        self.source_label.setText(tr("题集：{0} · {1} 道 · 独立完成 {2}/{1}",
                                     name, len(self.problems), completed))

    def _render(self) -> None:
        self.board.set_game(self.position)
        self.board.set_view_region(None if self._full_board else self._puzzle_region())
        self.board.set_analysis(None)
        self.board.set_interactive(bool(self.problems) and not self._finished)
        self._refresh_list()
        for button in (self.previous_button, self.next_button, self.random_button,
                       self.reset_button, self.hint_button, self.answer_button, self.view_button):
            button.setEnabled(bool(self.problems))

    def _puzzle_region(self) -> Optional[tuple[int, int, int, int]]:
        if not self.problems:
            return None
        problem = self.problems[self.problem_index]
        if not problem.root.children:
            return None
        points = [(x, y) for y, row in enumerate(problem.board.grid)
                  for x, color in enumerate(row) if color]
        def add_moves(node: TsumegoNode) -> None:
            if node.move is not None:
                points.append(node.move)
            for child in node.children:
                add_moves(child)
        add_moves(problem.root)
        if not points:
            return None
        xs, ys = zip(*points)
        return (max(0, min(xs) - 2), max(0, min(ys) - 2),
                min(problem.size - 1, max(xs) + 2), min(problem.size - 1, max(ys) + 2))

    def _toggle_view(self) -> None:
        self._full_board = not self._full_board
        self.view_button.setText(tr("聚焦局部" if self._full_board else "显示全盘"))
        self._render()

    def _play_step(self, step: TsumegoNode) -> None:
        color = self.position.to_move
        self.position.board = self.position.board.play(color, step.move, get_rules("chinese"))
        self.position.boards.append(self.position.board)
        record = MoveRecord(color, step.move)
        self.position.moves.append(record)
        self.position.last_move = record
        self.position.to_move = other(color)
        self.cursor = step

    def _record(self, status: str) -> None:
        key = self._problem_key()
        if self.progress.get(key) != "correct" and (
                self.progress.get(key) != "wrong" or status in ("correct", "wrong")):
            self.progress[key] = status
        self._save_progress()

    def _finish_if_terminal(self) -> bool:
        if self.cursor is None:
            return True
        verdict = self.cursor.verdict
        if verdict == "wrong":
            self._record("wrong")
            self._status("不正确，重做后再试")
        elif self.cursor.children:
            return False
        elif verdict == "correct":
            if not self.problems[self.problem_index].supported:
                self._record("unknown")
                self._status("劫题暂不支持可靠判定")
            elif self._assisted:
                self._record("viewed" if self._solution_used else "unknown")
                self._status("正解，但本次使用了提示或答案，不计独立完成")
            else:
                self._record("correct")
                self._status("正解，题目完成")
        else:
            self._record("unknown")
            self._status("答案未知，未计入完成")
        self._finished = True
        return True

    def _play_point(self, x: int, y: int) -> None:
        if self.cursor is None or self._finished:
            return
        if self.cursor is self.problems[self.problem_index].root and not self.cursor.children:
            try:
                board = self.position.board.play(self.position.to_move, (x, y), get_rules("chinese"))
                if len(self.position.boards) >= 2 and board == self.position.boards[-2]:
                    raise IllegalMove(tr("劫：不能立即提回"))
            except IllegalMove as e:
                self.status_label.setText(str(e))
                return
            record = MoveRecord(self.position.to_move, (x, y))
            self.position.board = board
            self.position.boards.append(board)
            self.position.moves.append(record)
            self.position.last_move = record
            self.position.to_move = other(self.position.to_move)
            self._status("研究局面：无可靠答案")
            self._render()
            return
        step = next((s for s in self.cursor.children if s.color == self.position.to_move
                     and s.move == (x, y)), None)
        if step is None:
            try:
                self.position.board.play(self.position.to_move, (x, y), get_rules("chinese"))
            except IllegalMove as e:
                self.status_label.setText(str(e))
                return
            self._record("unknown")
            self._status("不正确或答案未知：此着未列入题谱")
            self._render()
            return
        self._play_step(step)
        if not self._finish_if_terminal():
            reply = next((s for s in self.cursor.children if _has_correct(s)), None)
            if reply is None:
                reply = next((s for s in self.cursor.children if s.verdict != "wrong"), None)
            if reply:
                self._play_step(reply)
                if not self._finish_if_terminal():
                    self._status("继续 · 对方应于 {0}", to_gtp(reply.move, self.position.size))
            else:
                self._record("unknown")
                self._finished = True
                self._status("答案未知，未计入完成")
        self.board.set_preview([])
        self._render()

    def _step_problem(self, offset: int) -> None:
        indices = [self.problem_list.item(i).data(Qt.ItemDataRole.UserRole)
                   for i in range(self.problem_list.count())]
        if indices:
            current = indices.index(self.problem_index) if self.problem_index in indices else 0
            self.problem_index = indices[(current + offset) % len(indices)]
            self._load_problem()

    def _random_problem(self) -> None:
        indices = [self.problem_list.item(i).data(Qt.ItemDataRole.UserRole)
                   for i in range(self.problem_list.count())]
        if indices:
            self.problem_index = random.choice(indices)
            self._load_problem()

    def _reset(self) -> None:
        if self.problems:
            self._load_problem()

    def _hint(self) -> None:
        if self.cursor is None or self._finished:
            return
        step = next((s for s in self.cursor.children if _has_correct(s)), None)
        if step is None:
            self._status("当前没有可靠提示")
            return
        self._assisted = True
        self.records.setdefault(self._problem_key(), {})["hint"] = True
        self._save_progress()
        self.board.set_preview([to_gtp(step.move, self.position.size)])
        self._status("提示：{0}", to_gtp(step.move, self.position.size))

    def _show_answer(self) -> None:
        if self.cursor is None or self._finished:
            return
        step = next((s for s in self.cursor.children if _has_correct(s)), None)
        if step is None:
            self._status("当前没有可靠答案")
            return
        self._assisted = True
        self._solution_used = True
        self.records.setdefault(self._problem_key(), {})["solution"] = True
        self._record("viewed")
        self._play_step(step)
        if not self._finish_if_terminal():
            self._status("显示答案：{0}，再次点击继续", to_gtp(step.move, self.position.size))
        self._render()

    def retranslate_ui(self) -> None:
        self.title_label.setText(tr("死活题"))
        self.instructions.setText(tr("离线自制示例；也可导入 SGF。未标记终局的答案不会计为正解。"))
        for button, text in ((self.import_button, "导入 SGF 题集…"),
                             (self.previous_button, "上一题"), (self.next_button, "下一题"),
                             (self.random_button, "随机一题"), (self.reset_button, "重做"),
                             (self.hint_button, "提示"), (self.answer_button, "逐步显示答案")):
            button.setText(tr(text))
        self.view_button.setText(tr("聚焦局部" if self._full_board else "显示全盘"))
        current = self.filter_combo.currentData()
        self.filter_combo.blockSignals(True)
        self.filter_combo.clear()
        for text, value in (("全部题目", "all"), ("错题", "wrong"), ("未完成", "uncompleted")):
            self.filter_combo.addItem(tr(text), value)
        self.filter_combo.setCurrentIndex(max(0, self.filter_combo.findData(current)))
        self.filter_combo.blockSignals(False)
        if self.problems:
            path = str(self.source_path) if self.source_path else ""
            self._populate_collections()
            self.collection_combo.blockSignals(True)
            self.collection_combo.setCurrentIndex(max(0, self.collection_combo.findData(path)))
            self.collection_combo.blockSignals(False)
            if self.source_path is None:
                localize_default_tsumego(self.problems)
            self._populate_difficulties(self.difficulty_combo.currentIndex())
            self._update_problem_label()
            self._refresh_list()
        if self._status_data:
            source, args = self._status_data
            self._status(source, *args)
        if hasattr(self, "side_scroll"):
            self._update_layout(self.width(), self.height())
