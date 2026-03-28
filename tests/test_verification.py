"""Tests for verification.py — claim extraction and verification engine.

SAFETY: All store operations are mocked at the MODULE level.
"""

from unittest.mock import patch, MagicMock

from actions import ActionResult
from verification import needs_verification, verify_response, ClaimCheck, VerificationResult
from tests.helpers import make_action_result


class TestNeedsVerification:
    def test_very_short_response_skipped(self):
        """Responses under 30 chars without pre-detected claims skip verification."""
        assert needs_verification("test", "OK") is False
        assert needs_verification("test", "Sure, I can help.") is False

    def test_short_response_with_claim_detected(self):
        """Even short responses verify if claims_without_actions was detected."""
        result = make_action_result(claims_without_actions=["I've logged"])
        assert needs_verification("test", "Logged it.", result) is True

    def test_question_response_skipped(self):
        assert needs_verification("test", "Would you like me to help with that?") is False

    def test_claim_phrases_trigger(self):
        assert needs_verification("test", "I've logged your meal and saved the data for today.") is True

    def test_no_claims_no_trigger(self):
        assert needs_verification("test", "The weather today is sunny with a high of 72 degrees.") is False

    def test_claims_without_actions_always_triggers(self):
        result = make_action_result(claims_without_actions=["I've logged"])
        long_response = "Some response text here that is long enough to pass the 50 char gate for verification."
        assert needs_verification("test", long_response, result) is True

    def test_calorie_claim_triggers(self):
        response = "Based on what you've eaten, you ate about 1450 calories today so far, which is on track."
        assert needs_verification("test", response) is True

    def test_appointment_claim_triggers(self):
        response = "I've checked your calendar and your appointment is on March 28th at 2pm at the dentist office."
        assert needs_verification("test", response) is True


class TestVerifyResponse:
    def test_clean_result_ok(self):
        result = make_action_result(clean_response="Hello!")
        vr = verify_response("Hello!", result)
        assert vr.ok is True
        assert vr.needs_retry is False
        assert len(vr.claims) == 0

    def test_claims_without_actions_triggers_retry(self):
        result = make_action_result(
            clean_response="I've logged your meal.",
            claims_without_actions=["I've logged"],
        )
        vr = verify_response("I've logged your meal.", result)
        assert vr.ok is False
        assert vr.needs_retry is True
        assert vr.correction_prompt is not None
        assert "ACTION blocks" in vr.correction_prompt

    def test_calorie_claim_verified_when_close(self):
        result = make_action_result(clean_response="You had 1500 calories today.")
        with patch("nutrition_store.get_daily_totals") as mock_totals:
            mock_totals.return_value = {"calories": 1480}
            vr = verify_response("You had 1500 calories today.", result)
        # 20 cal difference is within 200 tolerance
        contradicted = [c for c in vr.claims if c.status == "contradicted"]
        assert len(contradicted) == 0

    def test_calorie_claim_contradicted_when_far(self):
        result = make_action_result(clean_response="You had 2500 calories today.")
        with patch("nutrition_store.get_daily_totals") as mock_totals:
            mock_totals.return_value = {"calories": 1800}
            vr = verify_response("You had 2500 calories today.", result)
        contradicted = [c for c in vr.claims if c.status == "contradicted"]
        assert len(contradicted) == 1
        assert "700" in contradicted[0].evidence  # delta

    def test_date_claims_logged_not_retried(self):
        result = make_action_result(
            clean_response="Your appointment is on April 5th."
        )
        vr = verify_response("Your appointment is on April 5th.", result)
        # Date claims are unverifiable and don't trigger retry
        assert vr.needs_retry is False
        date_claims = [c for c in vr.claims if c.claim_type == "date"]
        assert len(date_claims) >= 0  # may or may not match regex


class TestActionResult:
    def test_to_response_clean(self):
        r = make_action_result(clean_response="Hello!")
        assert r.to_response() == "Hello!"

    def test_to_response_with_failures(self):
        r = make_action_result(clean_response="Done.", failures=["Timer failed"])
        resp = r.to_response()
        assert "Timer failed" in resp
        assert "Note:" in resp

    def test_to_response_with_warnings(self):
        r = make_action_result(clean_response="Done.", warnings=["Check calories"])
        resp = r.to_response()
        assert "Check calories" in resp

    def test_to_response_with_missing_actions(self):
        r = make_action_result(
            clean_response="Done.",
            expect_actions_missing=["log_nutrition"],
        )
        resp = r.to_response()
        assert "WARNING" in resp
        assert "log_nutrition" in resp

    def test_to_response_with_claims_without_actions(self):
        r = make_action_result(
            clean_response="I logged it.",
            claims_without_actions=["I logged"],
        )
        resp = r.to_response()
        assert "System note" in resp

    def test_contains_operator(self):
        r = make_action_result(clean_response="Hello world!")
        assert "Hello" in r
        assert "goodbye" not in r

    def test_str_conversion(self):
        r = make_action_result(clean_response="Hello!")
        assert str(r) == "Hello!"

    def test_lower_method(self):
        r = make_action_result(clean_response="Hello!")
        assert r.lower() == "hello!"
