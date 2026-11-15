import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMenuBar, QRadioButton

from katago_gui.controller import State
from katago_gui.settings import Settings
from katago_gui.ui.main_window import MainWindow
from katago_gui.ui.board_widget import BoardWidget
from katago_gui.ui.theme import THEMES, widget_colors

from conftest import wait_state


def test_sidebar_settings_and_independent_review_preserve_play(qtbot, make_controller, tmp_path):
    controller = make_controller(board_size=9)
    window = MainWindow(controller.settings, controller=controller, ask=False)
    qtbot.addWidget(window)
    window.show()
    controller.game.play("B", (4, 4))
    window.current_path = tmp_path / "play.sgf"
    game = controller.game
    assert not window.findChildren(QMenuBar)
    assert window.btn_settings.parentWidget() is window.main_menu
    window.btn_settings.click()
    assert window.content_stack.currentWidget() is window.settings_page
    assert window.btn_settings.isChecked()
    window.btn_analysis.click()
    assert window.content_stack.currentWidget() is window.review_page
    assert window.btn_analysis.isChecked()
    window.btn_tsumego.click()
    assert window.content_stack.currentWidget() is window.tsumego_page
    window.btn_play.click()
    assert window.content_stack.currentWidget() is window.split
    assert controller.game is game and len(game.moves) == 1
    assert window.current_path == tmp_path / "play.sgf"


@pytest.mark.parametrize("theme", THEMES)
def test_live_theme_switch_preserves_game_and_persists(qtbot, make_controller, theme):
    controller = make_controller(board_size=9)
    window = MainWindow(controller.settings, controller=controller, ask=False)
    qtbot.addWidget(window)
    window.show()
    controller.start_engine(auto_ai_move=False)
    wait_state(qtbot, controller, State.READY)
    game, process = controller.game, controller.engine._proc
    window.enter_settings()
    combo = window.settings_page.theme_combo
    combo.setCurrentIndex(combo.findData(theme))
    assert controller.game is game and controller.engine._proc is process
    assert Settings.load().theme == theme
    assert widget_colors(window.board) is THEMES[theme]
    assert widget_colors(window.tsumego_page.board) is THEMES[theme]
    assert not window.grab().isNull()


def test_theme_save_failure_restores_selection(qtbot, isolated_home, monkeypatch):
    window = MainWindow(Settings(), ask=False)
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_save_settings", lambda: False)
    window.change_theme("light")
    assert window.settings.theme == "dark"
    assert window.settings_page.theme_combo.currentData() == "dark"
    assert window.property("theme") == "dark"


def test_background_job_guard_and_engine_restoration(qtbot, make_controller):
    controller = make_controller(board_size=9)
    window = MainWindow(controller.settings, controller=controller, ask=False)
    qtbot.addWidget(window)
    window.show()
    controller.start_engine(auto_ai_move=False)
    wait_state(qtbot, controller, State.READY)
    game = controller.game
    window._on_background_running(True)
    assert not controller.engine.running
    assert not window._can_start_background()
    assert not window.btn_new.isEnabled()
    assert not window.settings_page.engine_button.isEnabled()
    assert not window.analysis.button.isEnabled()
    assert not window.board.interactive
    window._on_background_running(False)
    wait_state(qtbot, controller, State.READY)
    assert controller.engine.running and controller.game is game


def test_settings_accessible_during_startup(qtbot, make_controller):
    controller = make_controller(board_size=9)
    window = MainWindow(controller.settings, controller=controller, ask=False)
    qtbot.addWidget(window)
    controller._set_state(State.STARTING)
    window.enter_settings()
    assert window.content_stack.currentWidget() is window.settings_page
    assert not window.settings_page.engine_button.isEnabled()
    assert not window._can_start_background()
    window.btn_tsumego.click()
    assert window.content_stack.currentWidget() is window.tsumego_page
    window.btn_analysis.click()
    assert window.content_stack.currentWidget() is window.review_page
    assert not window._can_start_background()
    window.btn_play.click()
    assert window.content_stack.currentWidget() is window.split


def test_compact_window_request_is_not_inflated(qtbot, isolated_home):
    window = MainWindow(Settings(), ask=False)
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    qtbot.addWidget(window)
    window.resize(800, 900)
    window.show()
    qtbot.wait(20)
    assert window.width() == 800
    for entry, page in ((window.enter_analysis_mode, window.review_page),
                        (window.enter_tsumego_mode, window.tsumego_page),
                        (window.enter_settings, window.settings_page)):
        entry()
        qtbot.wait(20)
        assert window.width() == 800
        assert window.content_stack.currentWidget() is page


def test_pending_command_line_sgf_requires_color_and_preserves_play(qtbot, isolated_home, tmp_path):
    window = MainWindow(Settings(), ask=False)
    qtbot.addWidget(window)
    original = window.ctl.game
    source = tmp_path / "review.sgf"
    source.write_text("(;FF[4]GM[1]SZ[9]KM[7.5];B[ee];W[cc])", encoding="utf-8")
    window.enter_analysis_mode()
    page = window.review_page
    page.set_pending_path(source)
    board = page.findChild(BoardWidget)
    assert board.game is None
    black = next(button for button in page.findChildren(QRadioButton) if "执黑" in button.text())
    black.click()
    qtbot.waitUntil(lambda: board.game is not None, timeout=1000)
    assert board.game.size == 9
    assert window.ctl.game is original
    assert len(page.current_position.moves) == 0


def test_300_move_review_navigation_meets_100ms_threshold(qtbot, isolated_home):
    from katago_gui.review import CandidateEvaluation, PositionEvaluation, parse_review_sgf
    from katago_gui.go.coords import to_gtp
    from katago_gui.go.game import GameRecord

    window = MainWindow(Settings(), ask=False)
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    qtbot.addWidget(window)
    window.resize(1280, 860)
    window.show()
    window.enter_analysis_mode()
    page = window.review_page
    game = GameRecord(size=19)
    nodes = []
    for _ in range(300):
        color = game.to_move
        point = next((x, y) for y in range(19) for x in range(19) if game.is_legal(color, (x, y)))
        game.play(color, point)
        nodes.append(f";{color}[{chr(97 + point[0])}{chr(97 + point[1])}]")
    record = parse_review_sgf("(;GM[1]FF[4]SZ[19]RU[Chinese]" + "".join(nodes) + ")")
    evaluations = [PositionEvaluation(.5, 0, 100) for _ in record.positions]
    for index, move in enumerate(record.moves):
        played = .49 if move.color == "B" else .51
        best = .51 if move.color == "B" else .49
        candidates = (CandidateEvaluation(to_gtp(move.move, 19), played, 0, 100),
                      CandidateEvaluation("pass", best, 0, 100))
        evaluations[index] = PositionEvaluation(.5, 0, 100, candidates)
    page.set_fixture(record, evaluations, "B")
    qtbot.wait(20)
    times = []
    for index in (1, 150, 300, 20, 299, 0):
        started = time.perf_counter()
        page.select_position(index)
        QApplication.processEvents()
        times.append(time.perf_counter() - started)
        assert len(page.current_position.moves) == index
    assert max(times) <= .1, f"Navigation took {max(times) * 1000:.1f} ms"
    page.chart.position_clicked.emit(123)
    assert len(page.current_position.moves) == 123
