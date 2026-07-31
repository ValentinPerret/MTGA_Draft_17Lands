from src.advisor_v2.model_router import ModelReviewRouter
from src.configuration import ModelAssistance


def enabled_config():
    return ModelAssistance(enabled=True)


def test_router_skips_decisive_or_late_reviews():
    router = ModelReviewRouter(enabled_config())
    assert not router.decide(score_margin=9.0, confidence=0.5).should_call
    assert not router.decide(
        score_margin=2.0, confidence=0.5, remaining_pick_seconds=10
    ).should_call


def test_router_requests_close_or_manual_review():
    router = ModelReviewRouter(enabled_config())
    assert router.decide(score_margin=2.0, confidence=0.5).should_call
    assert router.decide(
        score_margin=9.0, confidence=0.9, manual=True
    ).should_call


def test_router_uses_codex_default_without_explicit_model():
    router = ModelReviewRouter(ModelAssistance(enabled=True, model=""))
    decision = router.decide(score_margin=1.0, confidence=0.2)
    assert decision.should_call
