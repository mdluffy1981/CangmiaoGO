from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFormLayout, QLabel, QPushButton,
                               QTabWidget, QVBoxLayout, QWidget)

from ..i18n import tr
from ..settings import Settings
from .theme import THEME_NAMES
from .tuning_panel import TuningPanel


class SettingsPage(QWidget):
    theme_changed = Signal(str)
    language_changed = Signal(str)
    engine_requested = Signal()
    restart_requested = Signal()
    logs_requested = Signal()
    study_requested = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self.title = QLabel()
        self.title.setObjectName("sectionTitle")
        self.tabs = QTabWidget()
        self.engine_page = QWidget()
        engine_layout = QVBoxLayout(self.engine_page)
        self.engine_summary = QLabel()
        self.engine_summary.setWordWrap(True)
        self.engine_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.engine_button = QPushButton()
        self.restart_button = QPushButton()
        self.logs_button = QPushButton()
        self.study_button = QPushButton()
        for button, signal in (
            (self.engine_button, self.engine_requested), (self.restart_button, self.restart_requested),
            (self.logs_button, self.logs_requested), (self.study_button, self.study_requested),
        ):
            button.clicked.connect(signal.emit)
        engine_layout.addWidget(self.engine_summary)
        engine_layout.addWidget(self.engine_button)
        engine_layout.addWidget(self.restart_button)
        engine_layout.addWidget(self.study_button)
        engine_layout.addStretch(1)
        self.tuning = TuningPanel(settings)
        self.appearance = QWidget()
        form = QFormLayout(self.appearance)
        self.theme_label = QLabel()
        self.language_label = QLabel()
        self.theme_combo = QComboBox()
        for key in THEME_NAMES:
            self.theme_combo.addItem("", key)
        self.language_combo = QComboBox()
        self.language_combo.addItem("简体中文", "zh_CN")
        self.language_combo.addItem("English", "en")
        self.appearance_note = QLabel()
        self.appearance_note.setWordWrap(True)
        form.addRow(self.theme_label, self.theme_combo)
        form.addRow(self.language_label, self.language_combo)
        form.addRow(self.appearance_note)
        self.theme_combo.currentIndexChanged.connect(
            lambda _: self.theme_changed.emit(self.theme_combo.currentData()))
        self.language_combo.currentIndexChanged.connect(
            lambda _: self.language_changed.emit(self.language_combo.currentData()))
        self.diagnostics = QWidget()
        diagnostics_layout = QVBoxLayout(self.diagnostics)
        diagnostics_layout.addWidget(self.logs_button)
        diagnostics_layout.addStretch(1)
        for page in (self.engine_page, self.tuning, self.appearance, self.diagnostics):
            self.tabs.addTab(page, "")
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(self.tabs, 1)
        self.retranslate_ui()

    def sync(self) -> None:
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(self.settings.theme))
        self.theme_combo.blockSignals(False)
        self.language_combo.blockSignals(True)
        self.language_combo.setCurrentIndex(self.language_combo.findData(self.settings.language))
        self.language_combo.blockSignals(False)
        engine = self.settings.engine
        self.engine_summary.setText(tr("引擎：{0}\n模型：{1}\n配置：{2}",
                                      engine.executable or tr("未配置"),
                                      engine.model or tr("未配置"),
                                      engine.config or tr("未配置")))

    def set_busy(self, busy: bool) -> None:
        for button in (self.engine_button, self.restart_button, self.study_button):
            button.setEnabled(not busy)

    def retranslate_ui(self) -> None:
        self.title.setText(tr("设置"))
        for index, source in enumerate(("引擎与模型", "性能调优", "外观与语言", "日志与诊断")):
            self.tabs.setTabText(index, tr(source))
        self.engine_button.setText(tr("引擎设置…"))
        self.restart_button.setText(tr("重启引擎"))
        self.logs_button.setText(tr("查看日志…"))
        self.study_button.setText(tr("局面研究（自由落子）"))
        self.theme_label.setText(tr("界面主题"))
        self.language_label.setText(tr("界面语言"))
        for index, source in enumerate(THEME_NAMES.values()):
            self.theme_combo.setItemText(index, tr(source))
        self.language_combo.setItemText(0, tr("简体中文"))
        self.appearance_note.setText(tr("主题与语言立即生效并保存在本机，不改变棋局或引擎。"))
        self.sync()
        self.tuning.retranslate_ui()
