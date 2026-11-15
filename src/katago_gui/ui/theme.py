from string import Template

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QWidget


THEME_NAMES = {"dark": "深色", "light": "浅色", "chinese": "国风"}

COLORS = {
    "window": "#0F0F1A",
    "text": "#E8E8F0",
    "card": "#16213E",
    "border": "#2A2A44",
    "muted": "#B8B8D0",
    "primary": "#4A90D9",
    "secondary": "#1F3460",
    "danger": "#E74C3C",
    "panel": "#1E1E2E",
    "heading": "#89B4FA",
    "metric": "#F5F5FF",
    "hover": "#29467F",
    "disabled": "#242438",
    "disabled_text": "#8A8AA0",
    "alternate": "#1B2849",
    "scroll": "#4A4A62",
    "primary_text": "#0F0F1A",
    "board": "#DCB35C",
    "grid": "#3A2A10",
    "coordinate": "#4A3818",
    "success": "#34A853",
    "warning": "#F2C94C",
}

THEMES = {
    "dark": COLORS,
    "light": {
        **COLORS, "window": "#F5F7FA", "text": "#202733", "card": "#FFFFFF",
        "border": "#8C99AC", "muted": "#4D596B", "primary": "#245B9C",
        "secondary": "#E2EAF4", "danger": "#A52A35", "panel": "#FFFFFF",
        "heading": "#245B9C", "metric": "#202733", "hover": "#CDDDEF",
        "disabled": "#E5E8ED", "disabled_text": "#596579", "alternate": "#F0F4F8",
        "scroll": "#78879A", "primary_text": "#FFFFFF", "board": "#EDD29C",
        "grid": "#46351E", "coordinate": "#46351E", "success": "#226A38", "warning": "#805500",
    },
    "chinese": {
        **COLORS, "window": "#F3EBDD", "text": "#2B302D", "card": "#FAF5EB",
        "border": "#9B8970", "muted": "#5C584C", "primary": "#8F342A",
        "secondary": "#E6DDCB", "danger": "#922D25", "panel": "#F8F1E5",
        "heading": "#36584D", "metric": "#2B302D", "hover": "#D7CBB6",
        "disabled": "#E1D8C8", "disabled_text": "#625C50", "alternate": "#EEE5D5",
        "scroll": "#96836C", "primary_text": "#FFFFFF", "board": "#DFCA9F",
        "grid": "#423D30", "coordinate": "#423D30", "success": "#365F47", "warning": "#795400",
    },
}

# Candidate marks keyed by CandidateTier.name: fill colour and the text colour drawn on it.
CANDIDATE_COLORS = {"BEST": "#226A38", "HONTE": "#F2C94C", "NORMAL": "#245B9C"}
CANDIDATE_TEXT_COLORS = {"BEST": "#FFFFFF", "HONTE": "#1A1A1A", "NORMAL": "#FFFFFF"}


_STYLESHEET = Template("""
QMainWindow, QWidget#appRoot, QDialog {
    background: $window;
    color: $text;
    font-family: "Segoe UI", "Microsoft YaHei";
    font-size: 14px;
}
QWidget { color: $text; }
QLabel { color: $text; }
QLabel#mutedText { color: $muted; }
QLabel#sectionTitle { color: $heading; font-size: 16px; font-weight: 600; }
QLabel#metricValue { color: $metric; font-size: 16px; font-weight: 600; }
QFrame#banner { background: #FFF3CD; border: 1px solid #E0C060; border-radius: 6px; }
QFrame#banner QLabel { color: #4A3500; background: transparent; border: 0; }
QGroupBox, QTabWidget::pane {
    background: $panel;
    border: 1px solid $border;
    border-radius: 8px;
    margin-top: 8px;
}
QScrollArea#sideScroll, QWidget#sidePanel {
    background: $window;
}
QWidget#mainMenu {
    background: $panel;
    border: 1px solid $border;
    border-radius: 8px;
}
QWidget#mainMenu QPushButton { padding: 0 6px; }
QWidget#mainMenu QPushButton:checked {
    background: $primary;
    color: $primary_text;
    font-weight: 600;
}
QGroupBox::title {
    color: $heading;
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
}
QPushButton {
    min-height: 36px;
    padding: 0 12px;
    border: 1px solid $border;
    border-radius: 6px;
    background: $secondary;
    color: $text;
}
QPushButton:hover { background: $hover; }
QPushButton:focus { border: 1px solid $heading; }
QPushButton:disabled { background: $disabled; color: $disabled_text; }
QPushButton#dangerButton { background: $danger; color: #FFFFFF; }
QPushButton#primaryButton { background: $primary; color: $primary_text; font-weight: 600; }
QPushButton#dangerButton:disabled, QPushButton#primaryButton:disabled {
    background: $disabled; color: $disabled_text;
}
QRadioButton::indicator, QCheckBox::indicator {
    width: 14px; height: 14px;
    border: 1px solid $muted;
    background: $panel;
}
QRadioButton::indicator { border-radius: 8px; }
QRadioButton::indicator:checked, QCheckBox::indicator:checked { background: $primary; }
QWidget#sidePanel QPushButton { min-height: 30px; padding: 0 6px; }
QComboBox, QTableWidget, QProgressBar, QListWidget, QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QDoubleSpinBox, QSpinBox {
    background: $card;
    color: $text;
    border: 1px solid $border;
    selection-background-color: $primary;
    selection-color: #FFFFFF;
}
QProgressBar::chunk { background: $primary; }
QListWidget { alternate-background-color: $alternate; }
QListWidget::item:selected { background: $primary; color: $primary_text; }
QTableWidget { gridline-color: $border; }
QComboBox QAbstractItemView {
    background: $card;
    color: $text;
    selection-background-color: $primary;
    selection-color: $primary_text;
}
QHeaderView::section {
    background: $secondary;
    color: $text;
    border: 0;
    padding: 4px;
}
QTabBar::tab {
    background: $card;
    color: $muted;
    padding: 6px 14px;
}
QTabBar::tab:selected { color: $text; background: $secondary; }
QStatusBar { background: $card; color: $muted; }
QStatusBar QLabel { color: $muted; }
QStatusBar::item { border: none; }
QMenu { background: $card; color: $text; border: 1px solid $border; }
QMenu::item { padding: 5px 24px 5px 16px; }
QMenu::item:selected { background: $hover; }
QMenu::item:disabled { color: $disabled_text; }
QMenu::separator { height: 1px; background: $border; margin: 4px 8px; }
QToolTip { background: $card; color: $text; border: 1px solid $border; }
QSplitter::handle { background: $border; }
QScrollBar:vertical {
    background: $card;
    width: 12px;
}
QScrollBar::handle:vertical {
    background: $scroll;
    min-height: 24px;
    border-radius: 5px;
}
""")


def stylesheet(theme: str) -> str:
    return _STYLESHEET.substitute(THEMES[theme])


def apply_theme(widget: QWidget, theme: str) -> None:
    colors = THEMES[theme]
    widget.setProperty("theme", theme)
    palette = widget.palette()
    for role, key in (
        (QPalette.ColorRole.Window, "window"), (QPalette.ColorRole.WindowText, "text"),
        (QPalette.ColorRole.Base, "card"), (QPalette.ColorRole.AlternateBase, "alternate"),
        (QPalette.ColorRole.Text, "text"), (QPalette.ColorRole.Button, "secondary"),
        (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.Highlight, "primary"),
        (QPalette.ColorRole.HighlightedText, "primary_text"),
        (QPalette.ColorRole.ToolTipBase, "card"), (QPalette.ColorRole.ToolTipText, "text"),
    ):
        palette.setColor(role, QColor(colors[key]))
    widget.setPalette(palette)
    widget.setStyleSheet(stylesheet(theme))
    widget.update()


def widget_colors(widget: QWidget) -> dict[str, str]:
    current: QWidget | None = widget
    while current is not None:
        theme = current.property("theme")
        if isinstance(theme, str) and theme in THEMES:
            return THEMES[theme]
        current = current.parentWidget()
    return COLORS


APP_STYLESHEET = stylesheet("dark")
