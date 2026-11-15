import json

import pytest

from katago_gui.go.rules import get_rules
from katago_gui.sgf import SgfError
from katago_gui.tsumego import default_tsumego, load_tsumego, parse_tsumego
from katago_gui.ui.tsumego_page import TsumegoPage


def _correct_line(node):
    line = []
    while node.children:
        node = next(child for child in node.children if child.verdict != "wrong")
        line.append(node)
    return line


def test_default_problems_are_legal_self_authored_examples():
    problems = default_tsumego()
    assert [p.title for p in problems] == ["角上吃子", "边上吃子", "做出两只眼", "沿边追吃"]
    assert problems[2].goal == "黑先做活"
    for problem in problems:
        assert problem.source == "本项目自制示例"
        assert problem.license == "CC0-1.0"
        board = problem.board
        line = _correct_line(problem.root)
        for step in line:
            board = board.play(step.color, step.move, get_rules("chinese"))
        assert line[-1].verdict == "correct"
        if problem.title != "做出两只眼":
            assert board.captures[problem.to_move] > 0
    live = problems[2]
    assert [s.verdict for s in live.root.children] == ["correct", "wrong", "wrong"]
    assert len(_correct_line(problems[3].root)) == 5


def test_default_metadata_follows_language_but_imports_stay_verbatim(qtbot, tmp_path):
    from katago_gui.i18n import set_language

    page = TsumegoPage()
    qtbot.addWidget(page)
    set_language("en")
    page.retranslate_ui()
    assert page.problems[0].title == "Corner capture"
    assert "Self-authored" in page.problem_label.text()
    assert page.difficulty_combo.itemText(1) == "Beginner"
    path = tmp_path / "named.sgf"
    path.write_text("(;SZ[9]GN[角上吃子]PL[B];B[aa]C[Correct])", encoding="utf-8")
    page.load_problems(path)
    assert page.problems[0].title == "角上吃子"


def test_default_metadata_is_english_when_constructed_in_english(qtbot):
    from PySide6.QtWidgets import QComboBox, QLabel, QListWidget
    from katago_gui.i18n import set_language

    set_language("en")
    page = TsumegoPage()
    qtbot.addWidget(page)
    texts = [w.text() for w in page.findChildren(QLabel)]
    texts += [c.itemText(i) for c in page.findChildren(QComboBox) for i in range(c.count())]
    texts += [page.problem_list.item(i).text() for i in range(page.problem_list.count())]
    assert not any("\u4e00" <= ch <= "\u9fff" for text in texts for ch in text), texts
    page._step_problem(2)
    assert "Black to live" in page.problem_label.text()


@pytest.mark.parametrize("width, height", [(648, 900), (1214, 768), (500, 900), (648, 500)])
def test_layout_fits_and_board_clicks_map_to_points(qtbot, width, height):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtWidgets import QPushButton

    page = TsumegoPage()
    qtbot.addWidget(page)
    page.resize(width, height)
    page.show()
    qtbot.waitExposed(page)
    qtbot.wait(30)
    side = page.side_scroll.widget()
    assert side.width() <= page.side_scroll.viewport().width()
    for button in side.findChildren(QPushButton):
        assert button.width() >= button.minimumSizeHint().width()
        assert button.geometry().right() <= side.width()
    assert page.board.width() >= 300 and page.board.height() >= 300
    threshold = page._side_width() + page.board.minimumWidth() + page.split.handleWidth()
    assert (page.split.orientation() == Qt.Orientation.Vertical) == (width < threshold)
    center = page.board.center_of((0, 1))
    qtbot.mouseClick(page.board, Qt.MouseButton.LeftButton, pos=QPoint(round(center.x()), round(center.y())))
    assert page.progress[page._problem_key()] == "correct"


def test_content_identity_is_independent_of_filename_and_comments(tmp_path):
    a = tmp_path / "a.sgf"
    b = tmp_path / "b.sgf"
    a.write_text("(;SZ[9]PL[B];B[aa]C[Correct])", encoding="utf-8")
    b.write_text("(;SZ[9]PL[B]C[Notes];B[aa]C[Correct answer])", encoding="utf-8")
    assert load_tsumego(a)[0].problem_id == load_tsumego(b)[0].problem_id


def test_validates_all_branches_and_annotation_nodes():
    problem = parse_tsumego("(;SZ[9]PL[B](;B[aa];C[Correct])(;B[bb]C[Wrong])(;B[cc]))")[0]
    assert [s.verdict for s in problem.root.children] == ["correct", "wrong", "unknown"]
    with pytest.raises(SgfError):
        parse_tsumego("(;SZ[9]AB[aa]PL[B](;B[bb]C[Correct])(;B[aa]C[Wrong]))")
    with pytest.raises(SgfError):
        parse_tsumego("(;SZ[9]PL[B](;B[aa];W[bb])(;AB[cc];B[dd]))")
    ambiguous = parse_tsumego("(;SZ[9]PL[B](;B[aa]TE[1])"
                              "(;B[bb]C[This might be correct]))")[0]
    assert [s.verdict for s in ambiguous.root.children] == ["unknown", "unknown"]


def test_multi_solution_wrong_retry_and_unknown(qtbot, tmp_path):
    path = tmp_path / "multiple.sgf"
    path.write_text("(;SZ[9]PL[B](;B[aa]C[Correct])(;B[bb]C[Correct])"
                    "(;B[cc]C[Wrong])(;B[dd]))", encoding="utf-8")
    page = TsumegoPage()
    qtbot.addWidget(page)
    page.load_problems(path)
    page._play_point(2, 2)
    assert page.progress[page._problem_key()] == "wrong"
    page._reset()
    page._play_point(1, 1)
    assert page.progress[page._problem_key()] == "correct"
    page.progress.clear()
    page._reset()
    page._play_point(3, 3)
    assert page.progress[page._problem_key()] == "unknown"
    assert "未知" in page.status_label.text()
    page.progress.clear()
    page._reset()
    page._play_point(0, 0)
    assert page.progress[page._problem_key()] == "correct"


@pytest.mark.parametrize("action", ["_hint", "_show_answer"])
def test_assistance_is_recorded_but_not_independent(qtbot, action):
    page = TsumegoPage()
    qtbot.addWidget(page)
    key = page._problem_key()
    getattr(page, action)()
    if action == "_hint":
        page._play_point(0, 1)
        assert page.records[key]["hint"]
    else:
        assert page.records[key]["solution"]
    assert page.progress[key] != "correct"
    page._reset()
    page._play_point(0, 1)
    assert page.progress[key] == "correct"


def test_progress_survives_restart_and_file_move(qtbot, tmp_path):
    path = tmp_path / "one.sgf"
    path.write_text("(;SZ[9]PL[B];B[aa]C[Correct])"
                    "(;SZ[9]PL[B];B[bb]C[Correct])", encoding="utf-8")
    page = TsumegoPage()
    qtbot.addWidget(page)
    page.load_problems(path)
    page._step_problem(1)
    page._play_point(1, 1)
    key = page._problem_key()
    collection_id = page.collection_id
    moved = tmp_path / "moved.sgf"
    path.rename(moved)
    restarted = TsumegoPage()
    qtbot.addWidget(restarted)
    restarted.load_problems(moved)
    assert restarted._problem_key() == key
    assert restarted.collection_id == collection_id
    assert restarted.problem_index == 1
    assert restarted.progress[key] == "correct"


def test_resume_filters_and_legacy_migration(qtbot, tmp_path, isolated_home):
    path = tmp_path / "collection.sgf"
    path.write_text("(;SZ[9]PL[B];B[aa]C[Correct])"
                    "(;SZ[9]PL[B];B[bb]C[Wrong])", encoding="utf-8")
    isolated_home.mkdir(parents=True)
    (isolated_home / "tsumego_progress.json").write_text(
        json.dumps({f"{path.resolve()}#0": "correct", f"{path.resolve()}#1": "wrong"}),
        encoding="utf-8")
    page = TsumegoPage()
    qtbot.addWidget(page)
    page.load_problems(path)
    assert page.progress[page._problem_key()] == "unknown"
    page.filter_combo.setCurrentIndex(page.filter_combo.findData("wrong"))
    assert page.problem_list.count() == 1
    page.problem_list.setCurrentRow(0)
    assert page.problem_index == 1
    restarted = TsumegoPage()
    qtbot.addWidget(restarted)
    assert restarted.source_path == path.resolve()
    assert restarted.problem_index == 1
    restarted.filter_combo.setCurrentIndex(restarted.filter_combo.findData("uncompleted"))
    assert restarted.problem_list.count() == 2


def test_unmarked_tree_and_answerless_study_are_not_proven(qtbot, tmp_path):
    path = tmp_path / "unmarked.sgf"
    path.write_text("(;SZ[9]PL[B];B[aa])(;SZ[9]PL[W]AB[aa])", encoding="utf-8")
    page = TsumegoPage()
    qtbot.addWidget(page)
    page.load_problems(path)
    page._hint()
    assert "没有可靠提示" in page.status_label.text()
    page._play_point(0, 0)
    assert page.progress[page._problem_key()] == "unknown"
    page._step_problem(1)
    page._play_point(1, 1)
    assert page.position.board.get((1, 1)) == "W"
    assert page._problem_key() not in page.progress


def test_local_view_can_be_toggled_to_full_board(qtbot):
    page = TsumegoPage()
    qtbot.addWidget(page)
    assert page.board._bounds() == (0, 0, 3, 3)
    page._toggle_view()
    assert page.board._bounds() == (0, 0, 8, 8)
    page._toggle_view()
    assert page.board._bounds() == (0, 0, 3, 3)


def test_invalid_progress_is_not_overwritten(qtbot, isolated_home):
    isolated_home.mkdir(parents=True)
    progress = isolated_home / "tsumego_progress.json"
    progress.write_text("{bad json", encoding="utf-8")
    page = TsumegoPage()
    qtbot.addWidget(page)
    page._play_point(0, 1)
    assert progress.read_text(encoding="utf-8") == "{bad json"


def test_ko_problem_is_not_certified(qtbot, tmp_path):
    path = tmp_path / "ko.sgf"
    path.write_text("(;SZ[9]GN[Ko]PL[B];B[aa]C[Correct])", encoding="utf-8")
    page = TsumegoPage()
    qtbot.addWidget(page)
    page.load_problems(path)
    page._play_point(0, 0)
    assert page.progress[page._problem_key()] == "unknown"
