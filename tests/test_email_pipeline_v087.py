"""Tests for v0.8.7 email integration polish.

Covers: age gate, finding categories, Tier 2 score fix, check_subject_only,
surfacing tracker, priority scoring, stale cleanup, query.py --id, ask_model.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

import gmail_strategy
from monitors import classify_category, FINDING_CATEGORIES
from monitors.gmail import GmailMonitor, _email_age_hours


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _email(**kwargs):
    base = {
        "id": "msg_test_1",
        "from_address": "test@example.com",
        "from_name": "",
        "subject": "Test Subject",
        "body": "Test body content",
        "thread_id": "t1",
        "labels": ["UNREAD", "INBOX"],
        "gmail_category": None,
        "to_addresses": "",
        "data": {},
        "snippet": "",
        "timestamp": datetime.now(timezone.utc),
    }
    base.update(kwargs)
    return base


def _mock_conn():
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.execute.return_value.fetchone.return_value = None
    conn.execute.return_value.fetchall.return_value = []
    return conn


def _default_rules(**extra):
    rules = {
        "always_important": {"senders": [], "domains": []},
        "always_junk": {"senders": [], "domains": []},
        "content_overrides": [],
        "conversation_threads": [],
        "global_content_overrides": [],
    }
    rules.update(extra)
    return rules


# ---------------------------------------------------------------------------
# A4: Tier 2 Score Fix
# ---------------------------------------------------------------------------

class TestTier2NameWeight:
    """User name weight reduced from +2 to +1."""

    def _patch_db(self):
        conn = _mock_conn()
        return patch("db.get_conn", return_value=conn)

    def test_name_plus_corporate_is_routine(self):
        """corporate(+1) + name(+1) = 2 → routine, not important."""
        email = _email(
            from_address="deals@amazon.com",
            body="Hi Adam, rate your purchase",
        )
        with self._patch_db(), \
             patch("gmail_strategy.config") as mock_cfg:
            mock_cfg.OWNER_NAME = "Adam Smith"
            mock_cfg.OWNER_EMAIL = ""
            result = gmail_strategy._classify_tier2(email)
        assert result is not None
        assert result.classification == "routine"
        assert "Adam" in result.reason
        assert "(+1)" in result.reason

    def test_name_plus_replied_sender_is_important(self):
        """name(+1) + replied(+2) = 3 → important (correct)."""
        conn = _mock_conn()
        # First call: replied_to_sender check → found
        # Second call: entity_mentions → not found
        conn.execute.return_value.fetchone.side_effect = [
            {"1": 1},  # user has replied
            None,      # not in entity_mentions
        ]
        email = _email(from_address="friend@corp.com", body="Hey Adam")
        with patch("db.get_conn", return_value=conn), \
             patch("gmail_strategy.config") as mock_cfg:
            mock_cfg.OWNER_NAME = "Adam Smith"
            mock_cfg.OWNER_EMAIL = ""
            result = gmail_strategy._classify_tier2(email)
        assert result is not None
        assert result.classification == "important"


class TestBodyUnsubscribe:
    """Body 'unsubscribe' text subtracts 1 from score."""

    def _patch_db(self):
        conn = _mock_conn()
        return patch("db.get_conn", return_value=conn)

    def test_unsubscribe_in_body_subtracts_one(self):
        email = _email(
            from_address="news@corp.com",
            body="Latest updates. Click to unsubscribe.",
        )
        with self._patch_db(), \
             patch("gmail_strategy.config") as mock_cfg:
            mock_cfg.OWNER_NAME = ""
            mock_cfg.OWNER_EMAIL = ""
            result = gmail_strategy._classify_tier2(email)
        assert result is not None
        assert "unsubscribe" in result.reason.lower()

    def test_no_unsubscribe_no_penalty(self):
        email = _email(
            from_address="news@corp.com",
            body="Important information for you.",
        )
        with self._patch_db(), \
             patch("gmail_strategy.config") as mock_cfg:
            mock_cfg.OWNER_NAME = ""
            mock_cfg.OWNER_EMAIL = ""
            result = gmail_strategy._classify_tier2(email)
        assert result is not None
        assert "unsubscribe" not in result.reason.lower()


# ---------------------------------------------------------------------------
# A5: check_subject_only for Content Overrides
# ---------------------------------------------------------------------------

class TestCheckSubjectOnly:
    """Per-sender content_overrides with check_subject_only flag."""

    def test_body_match_ignored_with_flag(self):
        """Body keyword match should be ignored when check_subject_only is true."""
        rules = _default_rules(content_overrides=[{
            "sender_pattern": "paypal",
            "content_pattern": "payment|transaction",
            "classification": "important",
            "check_subject_only": True,
        }])
        email = _email(
            from_address="service@paypal.com",
            subject="Login from a new device",
            body="We noticed a payment method was used for login.",
        )
        with patch("gmail_strategy.load_rules", return_value=rules), \
             patch("gmail_strategy._user_participated_in_thread", return_value=False):
            result = gmail_strategy._classify_tier1(email)
        # Should NOT match — "payment" is only in body, not subject
        assert result is None

    def test_subject_match_works_with_flag(self):
        """Subject keyword match should still work with check_subject_only."""
        rules = _default_rules(content_overrides=[{
            "sender_pattern": "paypal",
            "content_pattern": "payment|transaction|\\$\\d",
            "classification": "important",
            "check_subject_only": True,
        }])
        email = _email(
            from_address="service@paypal.com",
            subject="Even Realities: $249.00 USD",
            body="Transaction details...",
        )
        with patch("gmail_strategy.load_rules", return_value=rules):
            result = gmail_strategy._classify_tier1(email)
        assert result is not None
        assert result.classification == "important"

    def test_without_flag_matches_body(self):
        """Without check_subject_only, body keywords should match normally."""
        rules = _default_rules(content_overrides=[{
            "sender_pattern": "paychex",
            "content_pattern": "benefits|password",
            "classification": "important",
        }])
        email = _email(
            from_address="comms@paychex.com",
            subject="Important notice",
            body="Review your benefits enrollment.",
        )
        with patch("gmail_strategy.load_rules", return_value=rules):
            result = gmail_strategy._classify_tier1(email)
        assert result is not None
        assert result.classification == "important"

    def test_paychex_turbotax_false_positive_fixed(self):
        """TurboTax promo from Paychex should NOT match with check_subject_only."""
        rules = _default_rules(content_overrides=[
            {
                "sender_pattern": "paychex",
                "content_pattern": "pay stub|paycheck|W-2|password|reset|benefits",
                "classification": "important",
                "check_subject_only": True,
            },
            {
                "sender_pattern": "paychex",
                "classification": "routine",
            },
        ])
        email = _email(
            from_address="comms@paychex.com",
            subject="Take the stress out of taxes with TurboTax",
            body="Import your W-2 from Paychex. Your tax filing made easy.",
        )
        with patch("gmail_strategy.load_rules", return_value=rules):
            result = gmail_strategy._classify_tier1(email)
        assert result is not None
        # Falls through to the sender-only fallback → routine
        assert result.classification == "routine"

    def test_paychex_password_reset_still_important(self):
        """Paychex password reset should still be caught."""
        rules = _default_rules(content_overrides=[{
            "sender_pattern": "paychex",
            "content_pattern": "password|reset|pay stub|benefits",
            "classification": "important",
            "check_subject_only": True,
        }])
        email = _email(
            from_address="comms@paychex.com",
            subject="Code to reset your Paychex Flex password",
        )
        with patch("gmail_strategy.load_rules", return_value=rules):
            result = gmail_strategy._classify_tier1(email)
        assert result is not None
        assert result.classification == "important"


# ---------------------------------------------------------------------------
# A1: Email Age Gate
# ---------------------------------------------------------------------------

class TestEmailAgeGate:
    def test_email_age_hours_fresh(self):
        ts = datetime.now(timezone.utc) - timedelta(hours=2)
        email = _email(timestamp=ts)
        age = _email_age_hours(email)
        assert age is not None
        assert 1.9 < age < 2.5

    def test_email_age_hours_old(self):
        ts = datetime.now(timezone.utc) - timedelta(hours=48)
        email = _email(timestamp=ts)
        age = _email_age_hours(email)
        assert age is not None
        assert age > 47

    def test_email_age_hours_no_timestamp(self):
        email = _email(timestamp=None)
        assert _email_age_hours(email) is None

    def test_fresh_email_creates_finding(self):
        """Email < 24h old → finding created."""
        fresh = _email(
            timestamp=datetime.now(timezone.utc) - timedelta(hours=1),
            from_name="Alice",
            subject="Important",
        )
        monitor = GmailMonitor()
        mock_result = gmail_strategy.ClassificationResult(
            "important", "tier1_hard", 1.0, "test", priority=3)
        with patch("gmail_store.get_unclassified", return_value=[fresh]), \
             patch("gmail_strategy.classify_email", return_value=mock_result), \
             patch("gmail_store.save_classification"):
            findings = monitor._classify_new_emails()
        assert len(findings) == 1
        assert findings[0].check_key == "email_important"

    def test_old_email_no_finding_but_classified(self):
        """Email > 24h old → classified (save_classification called) but no finding."""
        old = _email(
            timestamp=datetime.now(timezone.utc) - timedelta(hours=48),
            from_name="Alice",
            subject="Old email",
        )
        monitor = GmailMonitor()
        mock_result = gmail_strategy.ClassificationResult(
            "important", "tier1_hard", 1.0, "test", priority=3)
        with patch("gmail_store.get_unclassified", return_value=[old]), \
             patch("gmail_strategy.classify_email", return_value=mock_result), \
             patch("gmail_store.save_classification") as mock_save:
            findings = monitor._classify_new_emails()
        assert len(findings) == 0
        mock_save.assert_called_once()  # still classified


# ---------------------------------------------------------------------------
# A2: Email Finding Categories
# ---------------------------------------------------------------------------

class TestEmailFindingCategories:
    def test_email_urgent_is_category_c(self):
        assert FINDING_CATEGORIES["email_urgent"] == "C"

    def test_email_important_is_category_b(self):
        assert FINDING_CATEGORIES["email_important"] == "B"

    def test_classify_category_email_urgent(self):
        assert classify_category("email_urgent", "finding") == "C"

    def test_classify_category_email_important(self):
        assert classify_category("email_important", "finding") == "B"

    def test_urgent_email_uses_urgent_key(self):
        """Urgent classification → email_urgent check_key."""
        email = _email(
            timestamp=datetime.now(timezone.utc) - timedelta(minutes=5),
            from_name="Bank",
            subject="Fraud alert",
        )
        monitor = GmailMonitor()
        mock_result = gmail_strategy.ClassificationResult(
            "urgent", "tier1_hard", 1.0, "test", priority=1)
        with patch("gmail_store.get_unclassified", return_value=[email]), \
             patch("gmail_strategy.classify_email", return_value=mock_result), \
             patch("gmail_store.save_classification"):
            findings = monitor._classify_new_emails()
        assert len(findings) == 1
        assert findings[0].check_key == "email_urgent"
        assert findings[0].urgency == "urgent"

    def test_p1_email_uses_urgent_key(self):
        """Priority 1 (non-urgent classification) → email_urgent check_key."""
        email = _email(
            timestamp=datetime.now(timezone.utc) - timedelta(minutes=5),
            subject="Your verification code",
        )
        monitor = GmailMonitor()
        mock_result = gmail_strategy.ClassificationResult(
            "important", "tier1_hard", 0.95, "test", priority=1)
        with patch("gmail_store.get_unclassified", return_value=[email]), \
             patch("gmail_strategy.classify_email", return_value=mock_result), \
             patch("gmail_store.save_classification"):
            findings = monitor._classify_new_emails()
        assert len(findings) == 1
        assert findings[0].check_key == "email_urgent"


# ---------------------------------------------------------------------------
# A3: Stale Finding Cleanup
# ---------------------------------------------------------------------------

class TestStaleCleanup:
    def test_mark_delivered_bulk(self):
        from monitors import mark_delivered_bulk
        conn = _mock_conn()
        conn.execute.return_value.rowcount = 5
        with patch("monitors.db.get_conn", return_value=conn):
            mark_delivered_bulk("gmail", max_age_hours=24)
        # Should have executed an UPDATE
        conn.execute.assert_called_once()
        call_args = conn.execute.call_args
        assert "gmail" in call_args[0][1]
        assert "delivered = TRUE" in call_args[0][0]


# ---------------------------------------------------------------------------
# A9: Priority Scoring
# ---------------------------------------------------------------------------

class TestPriorityScoring:
    def _result(self, classification="important", category=None):
        return gmail_strategy.ClassificationResult(
            classification, "tier1_hard", 0.9, "test", category=category)

    def test_verification_code_is_p1(self):
        email = _email(subject="Your verification code is 123456")
        assert gmail_strategy._assign_priority(email, self._result()) == 1

    def test_urgent_classification_is_p1(self):
        email = _email(subject="Normal subject")
        result = self._result("urgent")
        assert gmail_strategy._assign_priority(email, result) == 1

    def test_out_for_delivery_is_p1(self):
        email = _email(subject="Your package is out for delivery")
        assert gmail_strategy._assign_priority(email, self._result()) == 1

    def test_payment_important_is_p2(self):
        email = _email(subject="Payment processed for $50")
        assert gmail_strategy._assign_priority(email, self._result()) == 2

    def test_watched_email_is_p2(self):
        email = _email(subject="Normal email")
        result = self._result(category="Watched")
        assert gmail_strategy._assign_priority(email, result) == 2

    def test_regular_important_is_p3(self):
        email = _email(subject="Hello from your friend")
        assert gmail_strategy._assign_priority(email, self._result()) == 3

    def test_routine_is_p4(self):
        email = _email(subject="Your daily digest")
        result = self._result("routine")
        assert gmail_strategy._assign_priority(email, result) == 4

    def test_junk_is_p4(self):
        email = _email(subject="50% off sale")
        result = self._result("junk")
        assert gmail_strategy._assign_priority(email, result) == 4

    def test_classify_email_sets_priority(self):
        """classify_email() should set priority on the result."""
        rules = _default_rules()
        rules["always_important"]["senders"].append("vip@test.com")
        email = _email(from_address="vip@test.com", subject="Hello")
        with patch("gmail_strategy.load_rules", return_value=rules):
            result = gmail_strategy.classify_email(email)
        assert result.priority == 3  # regular important


# ---------------------------------------------------------------------------
# A7: Email Body Access (query.py --id)
# ---------------------------------------------------------------------------

class TestQueryEmailById:
    def test_format_email_full(self):
        from query import format_email_full
        email = {
            "id": "msg123",
            "from_name": "Alice",
            "from_address": "alice@example.com",
            "to_addresses": "adam@example.com",
            "subject": "Hello",
            "timestamp": "2026-03-29 10:00:00",
            "has_attachments": False,
            "body": "This is the full email body.",
        }
        result = format_email_full(email)
        assert "msg123" in result
        assert "Alice" in result
        assert "Hello" in result
        assert "full email body" in result

    def test_format_email_full_none(self):
        from query import format_email_full
        assert format_email_full(None) == "Email not found."

    def test_cmd_email_with_id(self):
        from query import cmd_email
        email = {
            "id": "msg123", "from_name": "Bob", "from_address": "bob@x.com",
            "to_addresses": "", "subject": "Test", "timestamp": "",
            "has_attachments": False, "body": "Body text",
        }
        args = MagicMock()
        args.email_id = "msg123"
        args.search = None
        args.sender = None
        with patch("gmail_store.get_email", return_value=email) as mock_get:
            result = cmd_email(args)
        mock_get.assert_called_once_with("msg123")
        assert "Body text" in result


# ---------------------------------------------------------------------------
# A10: Tier 3 Model Upgrade (ask_model)
# ---------------------------------------------------------------------------

class TestAskModel:
    @pytest.mark.asyncio
    @patch("aria_api._get_client")
    async def test_ask_model_uses_specified_model(self, mock_client):
        import aria_api
        mock_api = MagicMock()
        mock_client.return_value = mock_api
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="routine")]
        mock_api.messages.create.return_value = mock_response

        result = await aria_api.ask_model("test", model="claude-sonnet-4-6")
        call_kwargs = mock_api.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-sonnet-4-6"
        assert result == "routine"

    @pytest.mark.asyncio
    @patch("aria_api._get_client")
    async def test_ask_haiku_backward_compat(self, mock_client):
        import aria_api
        mock_api = MagicMock()
        mock_client.return_value = mock_api
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="hello")]
        mock_api.messages.create.return_value = mock_response

        result = await aria_api.ask_haiku("test prompt")
        assert result == "hello"
        # Should use default Haiku model
        call_kwargs = mock_api.messages.create.call_args[1]
        assert "haiku" in call_kwargs["model"]
