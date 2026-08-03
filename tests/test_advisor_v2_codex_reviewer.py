import json
import subprocess
import time
from pathlib import Path

import pytest

from src.advisor.schema import Recommendation
from src.advisor_v2.codex_reviewer import (
    CodexReview,
    CodexReviewCoordinator,
    CodexReviewError,
    CodexReviewRunner,
    apply_codex_review,
    build_review_request,
)
from src.configuration import ModelAssistance


def recommendation(card_id, name, score, confidence=0.7):
    return Recommendation(
        card_name=name,
        card_id=card_id,
        base_win_rate=55.0,
        contextual_score=score,
        z_score=0.5,
        cast_probability=0.9,
        wheel_chance=0.0,
        functional_cmc=2.0,
        confidence=confidence,
        archetype_fit="UB Zombies",
        expected_to_make_deck=True,
        score_components={"marginal_deck": 3.2},
        reasoning=["Replaces a weaker two-drop."],
        lane_probabilities={"UB": 0.62, "BR": 0.18},
        engine="contextual_v2",
    )


def review_request():
    recommendations = [
        recommendation(101, "Local Choice", 70.0, 0.72),
        recommendation(202, "Alternative", 68.5, 0.66),
    ]
    request = build_review_request(
        event_name="CubeDraft_Planar_Innistrad",
        pack_number=2,
        pick_number=4,
        pack_cards=[
            {"arena_id": 101, "name": "Local Choice", "colors": ["U"]},
            {"arena_id": 202, "name": "Alternative", "colors": ["B"]},
        ],
        pool_cards=[{"name": "Pool Card", "colors": ["U"]}],
        recommendations=recommendations,
    )
    assert request is not None
    return request, recommendations


def valid_review(**overrides):
    data = {
        "recommended_card_id": 101,
        "agree_with_local_engine": True,
        "confidence": 0.8,
        "reason_codes": ["marginal_deck_upgrade", "supported_lane"],
        "short_explanation": "It is the strongest supported deck upgrade.",
        "key_uncertainty": "The lane could still pivot next pack.",
    }
    data.update(overrides)
    return CodexReview.model_validate(data)


def test_review_request_is_minimized_and_stable():
    first, _ = review_request()
    second, _ = review_request()

    assert first.digest == second.digest
    serialized = json.dumps(first.payload)
    assert "Player.log" not in serialized
    assert "draft_id" not in serialized
    assert "/Users/" not in serialized
    assert set(first.payload) == {
        "format",
        "pack",
        "pick",
        "cards_in_pack",
        "pool_summary",
        "candidate_decks",
        "candidate_scores",
        "lane_probabilities",
        "relevant_statistics",
    }


def test_apply_review_changes_order_only_at_sufficient_confidence():
    _, recommendations = review_request()
    changed = apply_codex_review(
        recommendations,
        valid_review(
            recommended_card_id=202,
            agree_with_local_engine=False,
            confidence=0.83,
        ),
    )
    assert changed.accepted and changed.changed_order
    assert recommendations[0].card_id == 202
    assert recommendations[0].model_assisted is True
    assert recommendations[0].contextual_score == 68.5

    _, recommendations = review_request()
    retained = apply_codex_review(
        recommendations,
        valid_review(
            recommended_card_id=202,
            agree_with_local_engine=False,
            confidence=0.4,
        ),
    )
    assert retained.accepted and not retained.changed_order
    assert recommendations[0].card_id == 101
    assert "retained" in recommendations[0].reasoning[0]


def test_runner_disables_tools_and_validates_pack_id(monkeypatch):
    request, _ = review_request()
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        result_path = Path(command[command.index("--output-last-message") + 1])
        result_path.write_text(valid_review().model_dump_json(), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = CodexReviewRunner(binary="/bin/echo")
    result = runner.review(request, timeout_seconds=1.0)

    assert result.recommended_card_id == 101
    command_text = " ".join(captured["command"])
    assert "features.shell_tool=false" in command_text
    assert 'web_search="disabled"' in command_text
    assert "--ephemeral" in captured["command"]
    assert "OPENAI_API_KEY" not in captured["env"]

    def fake_out_of_pack(command, **kwargs):
        result_path = Path(command[command.index("--output-last-message") + 1])
        result_path.write_text(
            valid_review(
                recommended_card_id=999,
                agree_with_local_engine=False,
            ).model_dump_json(),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_out_of_pack)
    with pytest.raises(CodexReviewError, match="outside the current pack"):
        runner.review(request, timeout_seconds=1.0)


def test_coordinator_is_nonblocking_cached_and_budgeted():
    request, _ = review_request()

    class FakeRunner:
        def review(self, request, **kwargs):
            time.sleep(0.02)
            return valid_review()

    coordinator = CodexReviewCoordinator(
        ModelAssistance(enabled=True, automatic_call_limit_per_draft=1),
        runner=FakeRunner(),
    )
    started = time.perf_counter()
    assert (
        coordinator.request(
            request, draft_key="draft", routing_reason="close candidates"
        )
        == "started"
    )
    assert time.perf_counter() - started < 0.1
    assert (
        coordinator.request(
            request, draft_key="draft", routing_reason="close candidates"
        )
        == "in_flight"
    )

    deadline = time.time() + 1.0
    while not coordinator.poll_completed() and time.time() < deadline:
        time.sleep(0.01)
    outcome = coordinator.outcome(request.digest)
    assert outcome is not None and outcome.status == "success"
    assert (
        coordinator.request(
            request, draft_key="draft", routing_reason="close candidates"
        )
        == "cached"
    )

    other = review_request()[0]
    other = type(other)(
        digest="f" * 64,
        payload=other.payload,
        allowed_card_ids=other.allowed_card_ids,
        local_top_card_id=other.local_top_card_id,
        local_top_confidence=other.local_top_confidence,
    )
    assert (
        coordinator.request(other, draft_key="draft", routing_reason="another state")
        == "limit_reached"
    )
