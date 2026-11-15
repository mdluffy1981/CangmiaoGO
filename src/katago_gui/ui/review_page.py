from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QGridLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..go.coords import other, to_gtp
from ..go.game import MoveRecord
from ..go.rules import get_rules
from ..review import (
    DEFAULT_VISITS,
    MoveReview,
    PositionEvaluation,
    ReviewAnalysisTask,
    ReviewPosition,
    ReviewRecord,
    load_review_sgf,
    move_reviews,
)
from ..i18n_review import review_tr
from ..settings import Settings
from ..sgf import SgfError
from .board_widget import BoardWidget
from .theme import widget_colors


class ReviewChart(QWidget):
    """Painted winrate plot with gaps for positions that have not been analyzed."""

    position_clicked = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.values: list[Optional[float]] = []
        self.labels: list[str] = []
        self.tooltips: list[str] = []
        self.selected = 0
        self._plot_cache: Optional[QPixmap] = None
        self._plot_cache_key = None
        self.setMinimumHeight(132)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

    def set_data(
        self,
        values: list[Optional[float]],
        labels: list[str],
        selected: int,
        tooltips: Optional[list[str]] = None,
    ) -> None:
        self.values = list(values)
        self.labels = list(labels)
        self.tooltips = list(tooltips or labels)
        self.selected = selected
        self._plot_cache = None
        self.update()

    def _plot_rect(self) -> QRectF:
        return QRectF(46, 14, max(1, self.width() - 62), max(1, self.height() - 40))

    def paintEvent(self, _event) -> None:
        colors = widget_colors(self)
        cache_key = (self.size(), self.devicePixelRatioF(), tuple(colors.items()))
        if self._plot_cache is not None and self._plot_cache_key == cache_key:
            painter = QPainter(self)
            painter.drawPixmap(0, 0, self._plot_cache)
            self._paint_cursor(painter, colors)
            painter.end()
            return
        ratio = self.devicePixelRatioF()
        pixmap = QPixmap(round(self.width() * ratio), round(self.height() * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(QColor(colors["panel"]))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(colors["panel"]))
        painter.setPen(QPen(QColor(colors["border"]), 1))
        painter.drawRoundedRect(self.rect().adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        plot = self._plot_rect()
        painter.setPen(QPen(QColor(colors["muted"]), 1))
        for value in (0, 0.5, 1):
            y = plot.bottom() - value * plot.height()
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.drawText(QRectF(2, y - 9, 40, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f"{value * 100:.0f}%")
        painter.setPen(QColor(colors["text"]))
        painter.drawText(QRectF(plot.left(), self.height() - 22, 52, 18), Qt.AlignmentFlag.AlignLeft,
                         "0")
        if self.values:
            painter.drawText(QRectF(plot.right() - 54, self.height() - 22, 54, 18),
                             Qt.AlignmentFlag.AlignRight, str(len(self.values) - 1))
        if len(self.values) > 1:
            last_point: Optional[QPointF] = None
            for index, value in enumerate(self.values):
                if value is None:
                    last_point = None
                    continue
                point = QPointF(
                    plot.left() + index * plot.width() / max(1, len(self.values) - 1),
                    plot.bottom() - value * plot.height(),
                )
                if last_point is not None:
                    painter.setPen(QPen(QColor(colors["primary"]), 2))
                    painter.drawLine(last_point, point)
                painter.setPen(QPen(QColor(colors["primary"]), 1))
                painter.setBrush(QColor(colors["primary"]))
                painter.drawEllipse(point, 3.2, 3.2)
                last_point = point
        painter.end()
        self._plot_cache = pixmap
        self._plot_cache_key = cache_key
        painter = QPainter(self)
        painter.drawPixmap(0, 0, pixmap)
        self._paint_cursor(painter, colors)
        painter.end()

    def _paint_cursor(self, painter: QPainter, colors: dict[str, str]) -> None:
        plot = self._plot_rect()
        if self.values and 0 <= self.selected < len(self.values):
            x = plot.left() + self.selected * plot.width() / max(1, len(self.values) - 1)
            painter.setPen(QPen(QColor(colors["warning"]), 1.5, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self.values:
            return
        plot = self._plot_rect()
        if not plot.contains(event.position()):
            return
        fraction = (event.position().x() - plot.left()) / max(1.0, plot.width())
        index = round(fraction * max(0, len(self.values) - 1))
        self.position_clicked.emit(max(0, min(len(self.values) - 1, index)))

    def mouseMoveEvent(self, event) -> None:
        if not self.values:
            return
        plot = self._plot_rect()
        if not plot.contains(event.position()):
            self.setToolTip("")
            return
        fraction = (event.position().x() - plot.left()) / max(1.0, plot.width())
        index = max(0, min(len(self.values) - 1, round(fraction * max(0, len(self.values) - 1))))
        self.setToolTip(self.tooltips[index] if index < len(self.tooltips) else self.labels[index])


class _BoardView:
    def __init__(self, record: ReviewRecord, position_index: int):
        self.size = record.size
        pos = record.position(position_index)
        self.board = pos.board
        self.to_move = pos.to_move
        self.moves = list(pos.moves)
        self.last_move: Optional[MoveRecord] = self.moves[-1] if self.moves else None


class ReviewPage(QWidget):
    message = Signal(str)
    error = Signal(str)
    running_changed = Signal(bool)

    def __init__(self, settings: Settings, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.settings = settings
        self.start_guard: Optional[Callable[[], bool]] = None
        self._record: Optional[ReviewRecord] = None
        self._human_color: Optional[str] = None
        self._pending_path: Optional[Path] = None
        self._evaluations: list[Optional[PositionEvaluation]] = []
        self._move_reviews: list[MoveReview] = []
        self._selected_position = 0
        self._running = False

        self._build_ui()
        self._task = ReviewAnalysisTask(settings.engine, visits=self.analysis_visits, parent=self)
        self._task.position_done.connect(self._on_position_done)
        self._task.progress.connect(self._on_progress)
        self._task.failed.connect(self.error)
        self._task.finished.connect(self._on_task_finished)
        self._update_controls()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def record(self) -> Optional[ReviewRecord]:
        return self._record

    @property
    def evaluations(self) -> list[Optional[PositionEvaluation]]:
        return list(self._evaluations)

    @property
    def selected_position(self) -> int:
        return self._selected_position

    @property
    def current_position(self) -> Optional[ReviewPosition]:
        if self._record is None:
            return None
        return self._record.position(self._selected_position)

    @property
    def analysis_visits(self) -> int:
        return self.visits_spin.value()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        controls = QGridLayout()
        controls.setHorizontalSpacing(8)
        controls.setVerticalSpacing(4)
        self.color_label = QLabel()
        self.color_black = QRadioButton()
        self.color_white = QRadioButton()
        self.color_buttons = QButtonGroup(self)
        self.color_buttons.setExclusive(True)
        self.color_buttons.addButton(self.color_black)
        self.color_buttons.addButton(self.color_white)
        self.color_black.clicked.connect(lambda: self.set_color("B"))
        self.color_white.clicked.connect(lambda: self.set_color("W"))
        self.import_button = QPushButton()
        self.import_button.clicked.connect(self.open_sgf)
        self.import_action = QAction(self)
        self.import_action.setShortcut(QKeySequence.StandardKey.Open)
        self.import_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.import_action.triggered.connect(self.open_sgf)
        self.addAction(self.import_action)
        self.start_button = QPushButton()
        self.start_button.setObjectName("primaryButton")
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_analysis)
        self.cancel_button = QPushButton()
        self.cancel_button.setObjectName("dangerButton")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        self.visits_label = QLabel()
        self.visits_spin = QSpinBox()
        self.visits_spin.setRange(64, 4096)
        self.visits_spin.setSingleStep(64)
        self.visits_spin.setValue(DEFAULT_VISITS)
        self.visits_spin.setToolTip(review_tr("每个局面的 KataGo 搜索访问数"))
        controls.addWidget(self.color_label, 0, 0)
        controls.addWidget(self.color_black, 0, 1)
        controls.addWidget(self.color_white, 0, 2)
        controls.addWidget(self.import_button, 0, 3)
        controls.addWidget(self.visits_label, 1, 0)
        controls.addWidget(self.visits_spin, 1, 1)
        controls.addWidget(self.start_button, 1, 2)
        controls.addWidget(self.cancel_button, 1, 3)
        controls.setColumnStretch(4, 1)
        outer.addLayout(controls)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setObjectName("mutedText")
        outer.addWidget(self.summary)
        self.status = QLabel()
        self.status.setObjectName("mutedText")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setMaximumHeight(8)
        outer.addWidget(self.progress_bar)

        self.moves_list = QListWidget()
        self.moves_list.setAlternatingRowColors(True)
        self.moves_list.currentItemChanged.connect(self._on_list_selection)
        self.detail_title = QLabel()
        self.detail_title.setObjectName("sectionTitle")
        self.detail_text = QLabel()
        self.detail_text.setWordWrap(True)
        self.detail_text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.detail_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        left_panel = QWidget()
        left_panel.setObjectName("sidePanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.moves_list, 3)
        left_layout.addWidget(self.detail_title)
        left_layout.addWidget(self.detail_text, 2)
        self.left_scroll = QScrollArea()
        self.left_scroll.setObjectName("sideScroll")
        self.left_scroll.setWidgetResizable(True)
        self.left_scroll.viewport().setAutoFillBackground(False)
        self.left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.left_scroll.setWidget(left_panel)
        self.left_scroll.setMinimumWidth(160)

        self.board = BoardWidget()
        self.board.setMinimumSize(340, 340)
        self.board.set_interactive(False)
        self.board.show_candidates = False
        board_panel = QWidget()
        board_layout = QVBoxLayout(board_panel)
        board_layout.setContentsMargins(0, 0, 0, 0)
        self.board_title = QLabel()
        self.board_title.setObjectName("sectionTitle")
        board_layout.addWidget(self.board_title)
        board_layout.addWidget(self.board, 1)

        self.workspace = QSplitter(Qt.Orientation.Horizontal)
        self.workspace.addWidget(self.left_scroll)
        self.workspace.addWidget(board_panel)
        self.workspace.setStretchFactor(0, 1)
        self.workspace.setStretchFactor(1, 3)
        self.workspace.setSizes([340, 800])

        self.chart = ReviewChart()
        self.chart.position_clicked.connect(self.select_position)
        self.chart_title = QLabel()
        self.chart_title.setObjectName("sectionTitle")
        lower = QWidget()
        lower_layout = QVBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(4)
        lower_layout.addWidget(self.chart_title)
        lower_layout.addWidget(self.chart)
        vertical = QSplitter(Qt.Orientation.Vertical)
        vertical.addWidget(self.workspace)
        vertical.addWidget(lower)
        vertical.setStretchFactor(0, 4)
        vertical.setStretchFactor(1, 1)
        outer.addWidget(vertical, 1)
        self.retranslate_ui()

    def set_color(self, color: str) -> None:
        if color not in ("B", "W"):
            raise ValueError("review color must be 'B' or 'W'")
        if self._running:
            return
        self._human_color = color
        self.color_black.setChecked(color == "B")
        self.color_white.setChecked(color == "W")
        self._refresh_summary()
        self._refresh_moves()
        self._show_position(self._selected_position)
        self._update_controls()
        if self._pending_path is not None:
            self.import_sgf(self._pending_path)

    def set_pending_path(self, path: Path) -> None:
        """Queue a command-line SGF until the mandatory review color is selected."""
        self._pending_path = Path(path)
        if self._human_color is not None:
            self.import_sgf(self._pending_path)

    def open_sgf(self) -> None:
        if self._human_color is None or self._running:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, review_tr("导入 SGF 棋谱…"), self.settings.last_sgf_dir, "SGF (*.sgf *.SGF);;All files (*)"
        )
        if path:
            self.import_sgf(path)

    def import_sgf(self, path: str | Path) -> bool:
        return self.load_path(path)

    def load_path(self, path: str | Path) -> bool:
        """Load an SGF into the review workspace after a color has been selected."""
        if self._human_color is None:
            self.message.emit(review_tr("请先选择复盘执色"))
            return False
        if self._running:
            self.message.emit(review_tr("整盘分析正在运行"))
            return False
        try:
            record = load_review_sgf(path)
        except (OSError, ValueError, UnicodeError, SgfError) as exc:
            self.error.emit(review_tr("加载棋谱失败：{0}", exc))
            return False
        self._record = record
        self._pending_path = None
        self.settings.last_sgf_dir = str(Path(path).expanduser().resolve().parent)
        self._evaluations = [None] * len(record.positions)
        self.progress_bar.setRange(0, max(1, len(record.positions)))
        self.progress_bar.setValue(0)
        self.status.setText("")
        self._selected_position = 0
        self._refresh_moves()
        self._refresh_summary()
        self._show_position(0)
        self._update_controls()
        for warning in record.warnings:
            self.message.emit(warning)
        return True

    def set_fixture(
        self,
        record: ReviewRecord,
        evaluations: list[Optional[PositionEvaluation]],
        human_color: str = "B",
    ) -> None:
        """Populate a pre-analyzed record for screenshots, previews, and UI tests."""
        if self._running:
            raise RuntimeError("Cannot replace the review fixture while analysis is running")
        if human_color not in ("B", "W"):
            raise ValueError("review color must be 'B' or 'W'")
        if len(evaluations) != len(record.positions):
            raise ValueError("a fixture needs exactly one evaluation slot per position")
        self._pending_path = None
        self._human_color = human_color
        self.color_black.setChecked(human_color == "B")
        self.color_white.setChecked(human_color == "W")
        self._record = record
        self._evaluations = list(evaluations)
        self.progress_bar.setRange(0, max(1, len(record.positions)))
        self.progress_bar.setValue(sum(value is not None for value in evaluations))
        self._selected_position = 0
        self._refresh_summary()
        self._refresh_moves()
        self._show_position(0)
        self._update_controls()

    def set_review_data(
        self,
        record: ReviewRecord,
        evaluations: list[Optional[PositionEvaluation]],
        human_color: str = "B",
    ) -> None:
        self.set_fixture(record, evaluations, human_color)

    def start_analysis(self) -> None:
        if self._record is None:
            self.message.emit(review_tr("没有选择棋谱"))
            return
        if self._human_color is None:
            self.message.emit(review_tr("请先选择复盘执色"))
            return
        if self._running:
            return
        if self.start_guard is not None and not self.start_guard():
            return
        self._task.visits = self.analysis_visits
        self._running = True
        self.running_changed.emit(True)
        self._update_controls()
        self._task.start(self._record)

    def cancel(self) -> None:
        if self._running:
            self._task.cancel()

    def wait_for_finished(self, timeout_ms: int = 5000) -> bool:
        """Cancel ongoing analysis and wait until the child process has exited."""
        if self._running:
            self.cancel()
        completed = self._task.wait_for_finished(timeout_ms)
        if completed and self._running:
            self._on_task_finished(False)
        return completed

    def _on_position_done(self, index: int, evaluation: PositionEvaluation) -> None:
        if index < len(self._evaluations):
            self._evaluations[index] = evaluation
            self._refresh_moves()
            self._show_position(self._selected_position)

    def _on_progress(self, complete: int, total: int) -> None:
        self.progress_bar.setRange(0, max(1, total))
        self.progress_bar.setValue(complete)
        self.status.setText(review_tr(
            "已分析 {0}/{1} 个局面（每局面 {2} 次访问）",
            complete, total, self._task.visits,
        ))

    def _on_task_finished(self, success: bool) -> None:
        if not self._running:
            return
        self._evaluations = self._task.evaluations
        self._running = False
        self.running_changed.emit(False)
        self._update_controls()
        if success:
            self.message.emit(review_tr("整盘分析完成。"))
        else:
            self.message.emit(review_tr("分析已取消；已完成的位置已保存，可继续分析。"))
        self._refresh_moves()
        self._show_position(self._selected_position)

    def _update_controls(self) -> None:
        has_color = self._human_color in ("B", "W")
        has_game = self._record is not None
        self.color_black.setEnabled(not self._running)
        self.color_white.setEnabled(not self._running)
        self.visits_spin.setEnabled(not self._running)
        self.import_button.setEnabled(has_color and not self._running)
        self.import_action.setEnabled(has_color and not self._running)
        complete = has_game and all(value is not None for value in self._evaluations)
        self.start_button.setEnabled(has_game and has_color and not self._running and not complete)
        self.cancel_button.setEnabled(self._running)
        self.start_button.setText(
            review_tr("继续分析" if any(value is not None for value in self._evaluations) else "开始整盘分析")
        )

    def _refresh_summary(self) -> None:
        if self._record is None:
            self.summary.setText(review_tr("尚未导入棋谱"))
            return
        record = self._record
        rules = get_rules(record.rules_key).label
        color = review_tr("黑") if self._human_color == "B" else review_tr("白")
        self.summary.setText(review_tr(
            "棋谱：{0} 手 | {1} 路 | {2} | 贴目 {3} | 视角：{4}",
            record.move_count, record.size, review_tr("规则：{0}", review_tr(rules)),
            f"{record.komi:g}", color,
        ))
        if record.result:
            self.summary.setText(f"{self.summary.text()} | {review_tr('结果：{0}', record.result)}")

    def _refresh_moves(self) -> None:
        old_index = self._selected_position
        self.moves_list.blockSignals(True)
        self.moves_list.clear()
        if self._record is not None:
            reviews = move_reviews(self._record, self._evaluations, self._human_color or "B")
            self._move_reviews = reviews
            for index in range(len(self._record.positions)):
                if index == 0:
                    label = review_tr("开局（落子前）")
                else:
                    move = self._record.moves[index - 1]
                    who = review_tr("黑") if move.color == "B" else review_tr("白")
                    where = review_tr("（停一手）") if move.move is None else to_gtp(move.move, self._record.size)
                    review = reviews[index - 1]
                    if review.winrate_change_view is None:
                        delta = review_tr("变化未知")
                    else:
                        delta = f"{review.winrate_change_view * 100:+.1f} pp"
                    if review.winrate_loss is None:
                        loss = review_tr("未评估")
                    else:
                        loss = review_tr("胜率损失 {0:.1f} 个百分点", review.winrate_loss * 100)
                    label = f"{index:>3}. {who} {where} | {delta} | {loss}"
                evaluation = self._evaluations[index] if index < len(self._evaluations) else None
                if evaluation is not None and evaluation.black_winrate is not None:
                    wr = evaluation.black_winrate if self._human_color != "W" else 1 - evaluation.black_winrate
                    label += f" | {wr * 100:.1f}%"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, index)
                self.moves_list.addItem(item)
        if self.moves_list.count():
            self._selected_position = max(0, min(old_index, self.moves_list.count() - 1))
            self.moves_list.setCurrentRow(self._selected_position)
        self.moves_list.blockSignals(False)
        self.chart.set_data(
            self._chart_values(), self._chart_labels(), self._selected_position,
            self._chart_tooltips(),
        )

    def _chart_values(self) -> list[Optional[float]]:
        values = []
        for evaluation in self._evaluations:
            if evaluation is None or evaluation.black_winrate is None:
                values.append(None)
            else:
                value = evaluation.black_winrate if self._human_color != "W" else 1 - evaluation.black_winrate
                values.append(value)
        return values

    def _chart_labels(self) -> list[str]:
        if self._record is None:
            return []
        labels = [review_tr("开局局面")]
        for index, move in enumerate(self._record.moves, 1):
            labels.append(f"{review_tr('第 {0} 手后', index)} {to_gtp(move.move, self._record.size) if move.move else 'pass'}")
        return labels

    def _chart_tooltips(self) -> list[str]:
        if self._record is None:
            return []
        color = self._human_color or "B"
        reviews = self._move_reviews
        labels = self._chart_labels()
        tooltips = []
        for index, value in enumerate(self._chart_values()):
            evaluation = self._evaluations[index] if index < len(self._evaluations) else None
            lines = [labels[index]]
            if evaluation is None or value is None:
                lines.append(review_tr("未知（尚无分析）"))
            else:
                lines.append(f"{review_tr('胜率')}：{value * 100:.1f}%")
                lines.append(f"{review_tr('访问数')}：{evaluation.visits}")
                if evaluation.visits < 100:
                    lines.append(review_tr("低样本"))
            if index:
                move = self._record.moves[index - 1]
                who = review_tr("黑") if move.color == "B" else review_tr("白")
                where = review_tr("（停一手）") if move.move is None else to_gtp(move.move, self._record.size)
                lines.append(f"{review_tr('落子方')}：{who} {where}")
                review = reviews[index - 1]
                if review.winrate_change_view is None:
                    lines.append(f"{review_tr('胜率变化')}：{review_tr('变化未知')}")
                else:
                    lines.append(review_tr(
                        "视角胜率变化 {0:+.1f} 个百分点",
                        review.winrate_change_view * 100,
                    ))
                if review.score_change_black is not None:
                    lines.append(review_tr("黑方目差变化 {0:+.1f} 目", review.score_change_black))
            tooltips.append("\n".join(lines))
        return tooltips

    def _on_list_selection(self, current: Optional[QListWidgetItem], _previous) -> None:
        if current is not None:
            self.select_position(int(current.data(Qt.ItemDataRole.UserRole)))

    def select_position(self, index: int) -> None:
        if self._record is None:
            return
        self._selected_position = max(0, min(index, len(self._record.positions) - 1))
        self.moves_list.blockSignals(True)
        self.moves_list.setCurrentRow(self._selected_position)
        self.moves_list.blockSignals(False)
        self.chart.selected = self._selected_position
        self.chart.update()
        self._show_position(self._selected_position)

    def _show_position(self, index: int) -> None:
        if self._record is None or not self._record.positions:
            self.detail_title.setText("")
            self.detail_text.setText("")
            self.board.game = None
            self.board.update()
            self.board_title.setText(review_tr("棋盘"))
            return
        index = max(0, min(index, len(self._record.positions) - 1))
        position = self._record.position(index)
        self.board.set_game(_BoardView(self._record, index))
        self.board.set_analysis(None)
        self.board.set_interactive(False)
        self.board_title.setText(
            review_tr("开局局面") if index == 0 else review_tr("第 {0} 手后", index)
        )
        evaluation = self._evaluations[index] if index < len(self._evaluations) else None
        if index == 0:
            self.detail_title.setText(review_tr("开局（落子前）"))
            detail = self._format_position(evaluation, position.to_move)
        else:
            move_review = self._move_reviews[index - 1]
            move = self._record.moves[index - 1]
            who = review_tr("黑") if move.color == "B" else review_tr("白")
            where = review_tr("（停一手）") if move.move is None else to_gtp(move.move, self._record.size)
            self.detail_title.setText(f"{review_tr('第 {0} 手后', index)}：{who} {where}")
            before = self._evaluations[index - 1]
            detail = self._format_move_review(move_review, before, evaluation, move.color)
        self.detail_text.setText(detail or review_tr("尚未导入棋谱"))

    def _format_position(
        self, evaluation: Optional[PositionEvaluation], to_move: str
    ) -> str:
        if evaluation is None:
            return review_tr("未知（尚无分析）")
        view_color = self._human_color or "B"
        if evaluation.black_winrate is None:
            winrate = review_tr("没有可用胜率")
        else:
            wr = evaluation.black_winrate if view_color == "B" else 1 - evaluation.black_winrate
            winrate = f"{review_tr('胜率')}：{wr * 100:.1f}%"
        score = evaluation.black_score_lead
        if score is not None and view_color == "W":
            score = -score
        score_text = f"{review_tr('目差')}：{score:+.1f}" if score is not None else f"{review_tr('目差')}：{review_tr('未知')}"
        player = review_tr("黑") if to_move == "B" else review_tr("白")
        low_sample = f" | {review_tr('低样本')}" if evaluation.visits < 100 else ""
        return f"{winrate}\n{score_text}\n{review_tr('落子方')}：{player}\n{review_tr('访问数')}：{evaluation.visits}{low_sample}"

    def _format_move_review(
        self,
        review: MoveReview,
        before: Optional[PositionEvaluation],
        after: Optional[PositionEvaluation],
        mover: str,
    ) -> str:
        lines = []
        if review.winrate_change_view is None:
            lines.append(f"{review_tr('胜率变化')}：{review_tr('未知（尚无分析）')}")
        else:
            lines.append(review_tr(
                "视角胜率变化 {0:+.1f} 个百分点", review.winrate_change_view * 100
            ))
            if review.winrate_change_black is not None:
                lines.append(review_tr(
                    "黑方胜率变化 {0:+.1f} 个百分点", review.winrate_change_black * 100
                ))
        if review.score_change_black is not None:
            lines.append(review_tr("黑方目差变化 {0:+.1f} 目", review.score_change_black))
        if review.best_move:
            lines.append(review_tr("最佳候选：{0}", review.best_move))
        lines.append(review_tr(
            "选点损失为落子方（{0}）视角",
            review_tr("黑") if mover == "B" else review_tr("白"),
        ))
        if review.played_candidate_known and review.winrate_loss is not None:
            lines.append(review_tr("胜率损失 {0:.1f} 个百分点", review.winrate_loss * 100))
            if review.score_loss is not None:
                lines.append(review_tr("目数损失 {0:.1f} 目", review.score_loss))
        else:
            lines.append(review_tr("未知（实战点未在同一局面候选中）"))
        perspective = self._human_color or "B"
        lines.append(review_tr(
            "前后评估均为{0}方视角",
            review_tr("黑") if perspective == "B" else review_tr("白"),
        ))
        if before is not None:
            lines.append(f"\n{review_tr('落子前')}：{self._position_brief(before, perspective)}")
        if after is not None:
            lines.append(f"{review_tr('落子后')}：{self._position_brief(after, perspective)}")
        return "\n".join(lines)

    def _position_brief(self, evaluation: PositionEvaluation, color: str) -> str:
        wr = evaluation.black_winrate
        if wr is not None and color == "W":
            wr = 1 - wr
        wr_text = f"{wr * 100:.1f}%" if wr is not None else review_tr("未知")
        score = evaluation.black_score_lead
        if score is not None and color == "W":
            score = -score
        score_text = f"{score:+.1f}" if score is not None else review_tr("未知")
        return f"{review_tr('胜率')} {wr_text}，{review_tr('目差')} {score_text}，{review_tr('访问数')} {evaluation.visits}"

    def retranslate_ui(self) -> None:
        self.color_label.setText(review_tr("分析视角") + "：")
        self.visits_label.setText(review_tr("搜索访问数"))
        self.color_black.setText(review_tr("你执黑"))
        self.color_white.setText(review_tr("你执白"))
        self.import_button.setText(review_tr("导入 SGF 棋谱…"))
        self.cancel_button.setText(review_tr("取消分析"))
        self.detail_title.setText(review_tr("逐手分析"))
        self.board_title.setText(review_tr("棋盘"))
        self.chart_title.setText(review_tr("胜率走势（悬停查看百分点涨跌）"))
        if not self._record:
            self.summary.setText(review_tr("尚未导入棋谱"))
        self._update_controls()
        self._refresh_summary()
        self._refresh_moves()
        self._show_position(self._selected_position)

    def apply_theme(self, _theme=None) -> None:
        self.chart.update()
        self.board.update()
