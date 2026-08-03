import os
import shutil
import tkinter
from types import SimpleNamespace

import pytest

from src.configuration import Configuration
from src.limited_sets import SetDictionary, SetInfo
from src.log_scanner import ArenaScanner
from src.ui.orchestrator import DraftOrchestrator
from src.ui.styles import Theme
from src.ui.windows.draft_practice import DraftPracticeWindow


OTJ_SNAPSHOT = os.path.join(
    os.getcwd(), "tests", "data", "OTJ_PremierDraft_Data_2024_5_3.json"
)


@pytest.fixture
def practice_env(tmp_path, monkeypatch):
    sets_dir = tmp_path / "Sets"
    temp_dir = tmp_path / "Temp"
    logs_dir = tmp_path / "Logs"
    sets_dir.mkdir()
    temp_dir.mkdir()
    logs_dir.mkdir()

    monkeypatch.setattr("src.constants.SETS_FOLDER", str(sets_dir))
    monkeypatch.setattr("src.constants.TEMP_FOLDER", str(temp_dir))
    monkeypatch.setattr("src.constants.DRAFT_LOG_FOLDER", str(logs_dir))

    dataset_name = "OTJ_PremierDraft_All_Data.json"
    shutil.copy(OTJ_SNAPSHOT, sets_dir / dataset_name)

    live_log = tmp_path / "Player.log"
    live_log.write_text("DETAILED LOGS: ENABLED\n", encoding="utf-8")
    set_list = SetDictionary(
        data={
            "Outlaws": SetInfo(
                arena=["OTJ"], seventeenlands=["OTJ"], set_code="OTJ"
            )
        }
    )
    live_scanner = ArenaScanner(
        str(live_log), set_list, sets_location=str(sets_dir), retrieve_unknown=False
    )
    config = Configuration()
    config.settings.arena_log_location = str(live_log)
    config.card_data.latest_dataset = dataset_name
    orchestrator = DraftOrchestrator(live_scanner, config, lambda: None)

    root = tkinter.Tk()
    Theme.apply(root, "Dark")
    app = SimpleNamespace(
        configuration=config,
        orchestrator=orchestrator,
        dashboard=SimpleNamespace(
            advisor_panel=SimpleNamespace(last_recs=[]),
        ),
        draft_practice_window=None,
    )

    yield root, app, live_scanner

    if orchestrator.is_practice_mode:
        orchestrator.end_practice_session()
    try:
        root.destroy()
    except tkinter.TclError:
        pass


def test_dummy_draft_uses_log_pipeline_and_restores_live_scanner(practice_env):
    root, app, live_scanner = practice_env
    window = DraftPracticeWindow(root, app)
    app.draft_practice_window = window
    root.update()

    assert app.orchestrator.is_practice_mode is True
    assert app.orchestrator.scanner is not live_scanner
    assert app.orchestrator.scanner.arena_file != live_scanner.arena_file

    practice_scanner = app.orchestrator.scanner
    assert practice_scanner.draft_start_search() is True
    assert practice_scanner.draft_data_search() is True
    assert practice_scanner.retrieve_current_limited_event() == ("OTJ", "PremierDraft")
    assert practice_scanner.retrieve_current_pack_and_pick() == (1, 1)
    assert len(practice_scanner.retrieve_current_pack_cards()) == 14

    picked_id = window._current_pack_ids[0]
    window._pick_card(picked_id)
    assert practice_scanner.draft_data_search() is True

    assert practice_scanner.retrieve_current_pack_and_pick() == (1, 2)
    assert practice_scanner.taken_cards == [picked_id]
    assert len(practice_scanner.retrieve_current_pack_cards()) == 13

    window.end_practice()
    assert app.orchestrator.is_practice_mode is False
    assert app.orchestrator.scanner is live_scanner
    assert live_scanner.retrieve_current_pack_and_pick() == (0, 0)


def test_practice_dataset_sync_does_not_change_persisted_selection(practice_env):
    root, app, _ = practice_env
    original_dataset = app.configuration.card_data.latest_dataset
    window = DraftPracticeWindow(root, app)
    app.draft_practice_window = window
    root.update()

    assert app.orchestrator.sync_dataset_to_event() is True
    assert app.configuration.card_data.latest_dataset == original_dataset

    window.end_practice()
