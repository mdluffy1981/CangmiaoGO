import pytest
from PySide6.QtGui import QColor
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QTextBrowser, QWidget

from katago_gui.go.game import GameRecord
from katago_gui.settings import THEME_IDS
from katago_gui.ui.board_widget import BoardWidget
from katago_gui.ui.theme import THEMES, apply_theme, stylesheet, widget_colors


def luminance(color):
    rgb = QColor(color).getRgbF()[:3]
    channels = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
    return sum(v * weight for v, weight in zip(channels, (.2126, .7152, .0722)))


@pytest.mark.parametrize("theme", THEMES)
def test_theme_contrast_and_inheritance(qtbot, theme):
    root = QWidget()
    child = QWidget(root)
    details = QTextBrowser(root)
    details.setHtml("<h3>Move 3</h3><p>Score change: -2.2 points</p>")
    qtbot.addWidget(root)
    apply_theme(root, theme)
    details.ensurePolished()
    colors = THEMES[theme]
    assert widget_colors(child) is colors
    assert "$" not in stylesheet(theme)
    assert details.palette().color(QPalette.ColorRole.Base).name() == colors["card"].lower()
    assert details.palette().color(QPalette.ColorRole.Text).name() == colors["text"].lower()
    assert set(THEMES) == set(THEME_IDS)
    for text, background in (("text", "window"), ("muted", "window"), ("primary_text", "primary")):
        a, b = sorted((luminance(colors[text]), luminance(colors[background])))
        assert (b + .05) / (a + .05) >= 4.5


@pytest.mark.parametrize("region", [(0, 0, 5, 5), (12, 13, 18, 18), (3, 4, 11, 14), None])
def test_cropped_board_preserves_coordinates(qtbot, region):
    board = BoardWidget()
    qtbot.addWidget(board)
    board.set_game(GameRecord(size=19))
    board.resize(700, 650)
    board.set_view_region(region)
    bounds = region or (0, 0, 18, 18)
    for point in ((bounds[0], bounds[1]), (bounds[2], bounds[3])):
        center = board.center_of(point)
        assert board.rect().contains(center.toPoint())
        assert board.point_at(center.x(), center.y()) == point
    assert not board.grab().isNull()
    with pytest.raises(ValueError):
        board.set_view_region((0, 0, 19, 19))
