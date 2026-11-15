from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from katago_gui.go.coords import from_gtp
from katago_gui.go.game import GameRecord
from katago_gui.review import (
    ReviewAnalysisTask,
    cache_key,
    move_reviews,
    normalize_winrate,
    parse_analysis_response,
    parse_review_sgf,
    position_request,
)
from katago_gui.settings import EngineSettings, Settings

FAKE_ANALYSIS = Path(__file__).with_name("fake_analysis_engine.py")


def _record(text: str = "(;GM[1]FF[4]SZ[9];B[aa];W[bb])"):
    return parse_review_sgf(text)


def _fake_builder(slow: bool = False):
    def build(_engine):
        args = ["-u", str(FAKE_ANALYSIS)]
        if slow:
            args.append("--slow")
        return sys.executable, args
    return build


def test_normalizes_black_white_and_side_to_move_perspectives():
    assert normalize_winrate(0.60, "BLACK") == pytest.approx(0.60)
    assert normalize_winrate(0.60, "WHITE") == pytest.approx(0.40)
    assert normalize_winrate(0.60, "SIDETOMOVE", "B") == pytest.approx(0.60)
    assert normalize_winrate(0.60, "SIDETOMOVE", "W") == pytest.approx(0.40)
    result = parse_analysis_response({
        "winratePerspective": "SIDETOMOVE",
        "rootInfo": {"currentPlayer": "W", "winrate": 0.40, "scoreLead": -3.0},
        "moveInfos": [{"move": "D4", "winrate": 0.35, "scoreLead": -4.0, "visits": 50}],
    })
    assert result.black_winrate == pytest.approx(0.60)
    assert result.black_score_lead == pytest.approx(3.0)
    assert result.candidates[0].black_winrate == pytest.approx(0.65)
    assert result.candidates[0].black_score_lead == pytest.approx(4.0)


def test_replays_setup_passes_explicit_to_move_and_end_state():
    record = parse_review_sgf(
        "(;GM[1]FF[4]SZ[9]AB[aa]AW[bb]PL[W]RE[B+R];W[];B[])"
    )
    assert len(record.positions) == record.move_count + 1 == 3
    assert record.positions[0].board.get(from_gtp("A9", 9)) == "B"
    assert record.positions[0].board.get(from_gtp("B8", 9)) == "W"
    assert record.positions[0].to_move == "W"
    assert [move.move for move in record.moves] == [None, None]
    assert record.positions[1].to_move == "B"
    assert record.positions[2].to_move == "W"
    assert record.finished
    request = position_request(record, 2, "request", 321)
    assert request["initialStones"] == [["B", "A9"], ["W", "B8"]]
    assert request["moves"] == [["W", "pass"], ["B", "pass"]]
    assert request["maxVisits"] == 321


def test_root_node_move_is_replayed_after_root_setup():
    record = parse_review_sgf("(;GM[1]FF[4]SZ[9]AB[aa]PL[W]W[bb];B[cc])")
    assert len(record.moves) == 2
    assert record.moves[0].color == "W"
    assert record.moves[0].move == from_gtp("B8", 9)
    assert record.positions[1].board.get(from_gtp("A9", 9)) == "B"
    assert record.positions[1].board.get(from_gtp("B8", 9)) == "W"
    assert record.positions[2].to_move == "W"


def test_midgame_setup_replaces_current_position_and_replays_remaining_moves():
    record = parse_review_sgf(
        "(;GM[1]FF[4]SZ[9];B[aa];AB[bb]PL[W];W[];B[])"
    )
    assert len(record.positions) == 4
    assert record.positions[1].board.get(from_gtp("B8", 9)) == "B"
    assert record.positions[1].to_move == "W"
    assert record.positions[1].analysis_moves == ()
    assert record.positions[2].analysis_moves == (("W", "pass"),)
    assert record.positions[3].analysis_moves == (("W", "pass"), ("B", "pass"))


def test_midgame_player_override_is_preserved_in_analysis_request():
    record = parse_review_sgf("(;GM[1]FF[4]SZ[9];B[aa];PL[W];W[])")
    assert record.positions[1].to_move == "W"
    assert record.positions[1].analysis_moves == ()
    assert record.positions[1].analysis_initial_player == "W"
    assert position_request(record, 1, "pl")["initialPlayer"] == "W"


def test_candidate_loss_is_unknown_when_played_move_was_not_returned():
    record = _record("(;GM[1]FF[4]SZ[9];B[aa])")
    evaluations = [
        parse_analysis_response({
            "rootInfo": {"currentPlayer": "B", "winrate": 0.6, "scoreLead": 2, "visits": 1000},
            "moveInfos": [{"move": "B9", "winrate": 0.7, "scoreLead": 4, "visits": 900}],
        }),
        parse_analysis_response({
            "rootInfo": {"currentPlayer": "W", "winrate": 0.3, "scoreLead": 3, "visits": 1000},
        }),
    ]
    review = move_reviews(record, evaluations, "B")[0]
    assert not review.played_candidate_known
    assert review.winrate_loss is None
    assert review.score_loss is None
    assert review.winrate_change_black == pytest.approx(-0.3)
    assert review.winrate_change_view == pytest.approx(-0.3)
    assert move_reviews(record, evaluations, "W")[0].winrate_change_view == pytest.approx(0.3)


def test_candidate_loss_compares_candidates_at_the_same_pre_move_position():
    record = _record("(;GM[1]FF[4]SZ[9];B[aa])")
    before = parse_analysis_response({
        "rootInfo": {"currentPlayer": "B", "winrate": 0.6, "scoreLead": 2, "visits": 100},
        "moveInfos": [
            {"move": "A9", "winrate": 0.55, "scoreLead": 1, "visits": 50},
            {"move": "B9", "winrate": 0.70, "scoreLead": 4, "visits": 50},
        ],
    })
    after = parse_analysis_response({
        "rootInfo": {"currentPlayer": "W", "winrate": 0.4, "scoreLead": 5, "visits": 100},
    })
    review = move_reviews(record, [before, after], "B")[0]
    assert review.played_candidate_known
    assert review.best_move == "B9"
    assert review.winrate_loss == pytest.approx(0.15)
    assert review.score_loss == pytest.approx(3)


def test_review_analysis_json_subprocess_and_resumable_cache(qtbot, tmp_path):
    record = _record()
    engine = EngineSettings("missing-katago", "missing-model", "missing-config")
    cache = tmp_path / "cache.json"
    task = ReviewAnalysisTask(engine, visits=333, command_builder=_fake_builder())
    with qtbot.waitSignal(task.finished, timeout=8000) as done:
        task.start(record, cache_file=cache)
    assert done.args == [True]
    assert [item.visits for item in task.evaluations if item is not None] == [333] * 3
    assert cache.is_file()
    assert len(task.evaluations) == len(record.moves) + 1

    resumed = ReviewAnalysisTask(engine, visits=333, command_builder=lambda _: pytest.fail("cache miss"))
    with qtbot.waitSignal(resumed.finished, timeout=1000) as resumed_done:
        resumed.start(record, cache_file=cache)
    assert resumed_done.args == [True]
    assert all(item is not None for item in resumed.evaluations)


def test_review_page_restores_cached_metrics_rows_details_and_chart(qtbot, tmp_path):
    from katago_gui.ui.review_page import ReviewPage

    path = tmp_path / "cached.sgf"
    path.write_text("(;GM[1]FF[4]SZ[9];B[aa];W[bb])", encoding="utf-8")
    settings = Settings()
    first = ReviewPage(settings)
    qtbot.addWidget(first)
    first.set_color("B")
    assert first.load_path(path)
    first._task.command_builder = _fake_builder()
    with qtbot.waitSignal(first.running_changed, check_params_cb=lambda running: not running):
        first.start_analysis()
    first.select_position(1)
    expected_chart = list(first.chart.values)
    expected_row = first.moves_list.item(1).text()
    expected_details = first.detail_text.text()

    resumed = ReviewPage(settings)
    qtbot.addWidget(resumed)
    resumed.set_color("B")
    assert resumed.load_path(path)
    resumed._task.command_builder = lambda _: pytest.fail("cached UI must not launch engine")
    resumed.start_analysis()
    resumed.select_position(1)
    assert not resumed.is_running
    assert all(value is not None for value in resumed.evaluations)
    assert resumed.chart.values == expected_chart == [0.6, 0.6, 0.6]
    assert resumed.moves_list.item(1).text() == expected_row
    assert "变化未知" not in expected_row
    assert resumed.detail_text.text() == expected_details
    assert "60.0%" in expected_details
    assert "尚无分析" not in expected_details
    assert resumed.progress_bar.value() == 3


def test_review_analysis_cancel_stops_child_and_keeps_completed_cache(qtbot, tmp_path):
    record = _record("(;GM[1]FF[4]SZ[9])")
    task = ReviewAnalysisTask(
        EngineSettings(), command_builder=_fake_builder(slow=True)
    )
    finished = []
    task.finished.connect(finished.append)
    task.start(record, cache_file=tmp_path / "partial.json")
    qtbot.waitUntil(lambda: task.process is not None and task.process.state().value != 0, timeout=3000)
    task.cancel()
    assert not task.is_running
    assert finished == [False]
    assert task.evaluations == [None]
    assert task.process is None


def test_review_page_requires_color_preserves_import_and_gates_engine_start(qtbot, tmp_path):
    from katago_gui.ui.review_page import ReviewPage

    sgf_path = tmp_path / "review.sgf"
    sgf_path.write_text("(;GM[1]FF[4]SZ[9];B[aa])", encoding="utf-8")
    page = ReviewPage(Settings())
    qtbot.addWidget(page)
    assert not page.import_button.isEnabled()
    assert not page.start_button.isEnabled()
    assert not page.cancel_button.isEnabled()
    assert page.analysis_visits == 128
    assert page.visits_spin.minimum() == 64
    assert page.visits_spin.maximum() == 4096
    assert not page.import_sgf(str(sgf_path))
    assert page.record is None
    page.set_color("B")
    assert page.import_button.isEnabled()
    assert page.import_sgf(str(sgf_path))
    assert page.record.move_count == 1
    assert not page.board.interactive
    assert page.start_button.isEnabled()


def test_review_visit_budget_is_applied_to_worker_and_controls_disable_during_run(qtbot, tmp_path):
    from katago_gui.ui.review_page import ReviewPage

    sgf_path = tmp_path / "review.sgf"
    sgf_path.write_text("(;GM[1]FF[4]SZ[9])", encoding="utf-8")
    page = ReviewPage(Settings())
    qtbot.addWidget(page)
    page.set_color("B")
    assert page.import_sgf(sgf_path)
    page._task.command_builder = _fake_builder(slow=True)
    page.visits_spin.setValue(256)
    page.start_guard = lambda: True
    page.start_analysis()
    assert page._task.visits == 256
    assert not page.visits_spin.isEnabled()
    qtbot.waitUntil(lambda: page._task.process is not None, timeout=3000)
    assert page.wait_for_finished(3000)
    assert page.visits_spin.isEnabled()

    guarded = []
    page.start_guard = lambda: guarded.append(not page.is_running) or False
    page.start_analysis()
    assert guarded == [True]
    assert not page.is_running
    assert page.start_button.isEnabled()


def test_pending_sgf_waits_for_color_and_import_shortcut_is_scoped(qtbot, tmp_path):
    from katago_gui.ui.review_page import ReviewPage

    sgf_path = tmp_path / "pending.sgf"
    sgf_path.write_text("(;GM[1]FF[4]SZ[9];B[aa])", encoding="utf-8")
    page = ReviewPage(Settings())
    qtbot.addWidget(page)
    page.set_pending_path(sgf_path)
    assert page.record is None
    assert not page.import_action.isEnabled()
    page.set_color("W")
    assert page.record is not None
    assert page.record.move_count == 1
    assert page.import_action.isEnabled()
    assert page.import_action.shortcut().toString() == "Ctrl+O"
    assert page.current_position is page.record.positions[0]


def test_review_page_fixture_prefills_results_without_running_engine(qtbot):
    from katago_gui.ui.review_page import ReviewPage
    from katago_gui.review import PositionEvaluation

    record = _record()
    evaluations = [
        PositionEvaluation(0.60, 2.0, 100, ()),
        PositionEvaluation(0.55, 1.0, 100, ()),
        PositionEvaluation(0.50, 0.0, 100, ()),
    ]
    page = ReviewPage(Settings())
    qtbot.addWidget(page)
    page.set_fixture(record, evaluations, "W")
    page.select_position(2)
    assert page.selected_position == 2
    assert page.current_position is record.positions[2]
    assert page.chart.values == pytest.approx([0.40, 0.45, 0.50])
    page.chart.position_clicked.emit(1)
    assert page.selected_position == 1
    assert not page.is_running


@pytest.mark.parametrize(
    ("color", "before", "after", "delta", "before_score", "after_score"),
    [
        ("B", "60.0%", "55.0%", "-5.0", "+2.0", "+1.0"),
        ("W", "40.0%", "45.0%", "+5.0", "-2.0", "-1.0"),
    ],
)
def test_review_before_after_details_use_selected_color(
    qtbot, color, before, after, delta, before_score, after_score
):
    from katago_gui.review import PositionEvaluation
    from katago_gui.ui.review_page import ReviewPage

    page = ReviewPage(Settings())
    qtbot.addWidget(page)
    page.set_fixture(
        _record("(;GM[1]FF[4]SZ[9];B[aa])"),
        [PositionEvaluation(0.60, 2, 100), PositionEvaluation(0.55, 1, 100)],
        color,
    )
    page.select_position(1)
    text = page.detail_text.text()
    before_line = next(line for line in text.splitlines() if line.startswith("落子前"))
    after_line = next(line for line in text.splitlines() if line.startswith("落子后"))
    assert before in before_line and before_score in before_line
    assert after in after_line and after_score in after_line
    assert f"视角胜率变化 {delta} 个百分点" in text


def test_review_page_retranslates_module_catalog(qtbot):
    from katago_gui.i18n import set_language
    from katago_gui.ui.review_page import ReviewPage

    page = ReviewPage(Settings())
    qtbot.addWidget(page)
    try:
        set_language("en")
        page.retranslate_ui()
        assert page.color_black.text() == "I play Black"
        assert page.import_button.text() == "Import SGF…"
        assert page.chart_title.text() == "Winrate trend (hover for percentage-point changes)"
    finally:
        set_language("zh_CN")
        page.retranslate_ui()


def test_three_hundred_move_fixture_navigation_is_quick(qtbot):
    from katago_gui.review import PositionEvaluation
    from katago_gui.ui.review_page import ReviewPage

    text = "(;GM[1]FF[4]SZ[9]" + ";B[];W[]" * 150 + ")"
    record = parse_review_sgf(text)
    results = [PositionEvaluation(0.5, 0.0, 100) for _ in record.positions]
    page = ReviewPage(Settings())
    qtbot.addWidget(page)
    page.set_fixture(record, results)
    start = time.perf_counter()
    page.select_position(300)
    elapsed = time.perf_counter() - start
    assert page.selected_position == 300
    assert elapsed < 0.1


def test_review_page_emits_running_before_child_start_and_gates_inputs(qtbot, tmp_path):
    from katago_gui.ui.review_page import ReviewPage

    sgf_path = tmp_path / "review.sgf"
    sgf_path.write_text("(;GM[1]FF[4]SZ[9])", encoding="utf-8")
    page = ReviewPage(Settings())
    qtbot.addWidget(page)
    page.set_color("W")
    assert page.import_sgf(str(sgf_path))
    page._task.command_builder = _fake_builder(slow=True)
    order = []
    page.start_guard = lambda: order.append("guard") or True
    page.running_changed.connect(
        lambda value: order.append(("running", value, page._task.process))
    )
    page.start_analysis()
    assert page.is_running
    assert not page.import_button.isEnabled()
    assert not page.color_black.isEnabled()
    assert page.cancel_button.isEnabled()
    assert order[0] == "guard"
    assert order[1][0:2] == ("running", True)
    assert order[1][2] is None
    qtbot.waitUntil(lambda: page._task.process is not None, timeout=3000)
    assert page.wait_for_finished(3000)
    assert not page.is_running
    assert order[-1][0:2] == ("running", False)


def test_review_cache_identity_includes_game_engine_model_and_search_budget(tmp_path):
    record = _record()
    exe, model, config = (tmp_path / name for name in ("katago", "model.bin.gz", "config.cfg"))
    exe.write_bytes(b"engine")
    model.write_bytes(b"model")
    config.write_text("config", encoding="utf-8")
    engine = EngineSettings(str(exe), str(model), str(config))
    first = cache_key(record, engine, 1000)
    assert cache_key(record, engine, 1001) != first
    assert cache_key(_record("(;GM[1]FF[4]SZ[9];B[aa])"), engine, 1000) != first
    model.write_bytes(b"changed")
    assert cache_key(record, engine, 1000) != first


def test_importing_review_does_not_mutate_existing_game_record():
    existing = GameRecord(size=9)
    existing.play("B", from_gtp("A9", 9))
    original_moves = list(existing.moves)
    review = _record()
    assert review.move_count == 2
    assert existing.moves == original_moves


@pytest.mark.real_engine
@pytest.mark.skipif(
    os.environ.get("KATAGO_RUN_REAL_TESTS") != "1",
    reason="set KATAGO_RUN_REAL_TESTS=1 to run the bounded real KataGo JSON protocol check",
)
def test_real_katago_json_analysis_protocol(qtbot, tmp_path):
    root = Path(__file__).resolve().parents[1] / "build" / "pyi-dist" / "KataGoDesktop" / "engine"
    executable = Path(os.environ.get(
        "KATAGO_REVIEW_EXECUTABLE", str(root / "eigen" / "katago.exe")
    ))
    model_path = os.environ.get("KATAGO_REVIEW_MODEL")
    model = Path(model_path) if model_path else next(iter(root.glob("*.bin.gz")), None)
    config = Path(os.environ.get("KATAGO_REVIEW_CONFIG", str(root / "gtp.cfg")))
    if not executable.is_file() or model is None or not config.is_file():
        pytest.skip("bundled Eigen KataGo executable, model, and config are unavailable")
    record = _record("(;GM[1]FF[4]SZ[9]KM[7.5];B[ee];W[de])")
    visits = int(os.environ.get("KATAGO_REVIEW_VISITS", "12"))
    task = ReviewAnalysisTask(
        EngineSettings(str(executable), str(model), str(config)), visits=visits
    )
    errors = []
    task.failed.connect(errors.append)
    with qtbot.waitSignal(task.finished, timeout=90_000) as done:
        task.start(record, cache_file=tmp_path / "real-cache.json")
    assert done.args == [True], errors
    assert len(task.evaluations) == 3
    assert all(value is not None for value in task.evaluations)
    assert all(0 <= value.black_winrate <= 1 for value in task.evaluations if value)
    assert all(value.visits > 0 for value in task.evaluations if value)
    assert any(value.candidates for value in task.evaluations if value)
