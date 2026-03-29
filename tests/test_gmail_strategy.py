"""Tests for gmail_strategy.py — 3-tier email classification engine."""

from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import gmail_strategy


class TestDefaultRules:
    def test_default_rules_structure(self):
        rules = gmail_strategy._default_rules()
        assert "always_important" in rules
        assert "always_junk" in rules
        assert "content_overrides" in rules
        assert "conversation_threads" in rules


class TestLoadRules:
    def test_returns_defaults_when_file_missing(self):
        with patch("gmail_strategy.config") as mock_config:
            mock_config.DATA_DIR = MagicMock()
            mock_config.GMAIL_RULES_FILE = "/nonexistent/path.yaml"
            with patch("gmail_strategy.Path") as mock_path:
                mock_path.return_value.exists.return_value = False
                gmail_strategy._rules_cache = None
                rules = gmail_strategy.load_rules()
        assert rules["always_important"]["senders"] == []


class TestTier1HardRules:
    def _email(self, from_addr="test@example.com", subject="Test",
               body="", thread_id="t1", from_name=""):
        return {
            "from_address": from_addr,
            "from_name": from_name,
            "subject": subject,
            "body": body,
            "thread_id": thread_id,
            "labels": [],
            "gmail_category": None,
        }

    def test_always_important_sender(self):
        rules = {
            "always_important": {"senders": ["vip@example.com"], "domains": []},
            "always_junk": {"senders": [], "domains": []},
            "content_overrides": [],
            "conversation_threads": [],
        }
        with patch("gmail_strategy.load_rules", return_value=rules):
            result = gmail_strategy._classify_tier1(self._email(from_addr="vip@example.com"))
        assert result is not None
        assert result.classification == "important"
        assert result.tier == "tier1_hard"

    def test_always_important_domain(self):
        rules = {
            "always_important": {"senders": [], "domains": ["bankerwire.com"]},
            "always_junk": {"senders": [], "domains": []},
            "content_overrides": [],
            "conversation_threads": [],
        }
        with patch("gmail_strategy.load_rules", return_value=rules):
            result = gmail_strategy._classify_tier1(self._email(from_addr="hr@bankerwire.com"))
        assert result is not None
        assert result.classification == "important"

    def test_always_junk_sender(self):
        rules = {
            "always_important": {"senders": [], "domains": []},
            "always_junk": {"senders": ["spam@junk.com"], "domains": []},
            "content_overrides": [],
            "conversation_threads": [],
        }
        with patch("gmail_strategy.load_rules", return_value=rules):
            result = gmail_strategy._classify_tier1(self._email(from_addr="spam@junk.com"))
        assert result is not None
        assert result.classification == "junk"

    def test_always_junk_domain(self):
        rules = {
            "always_important": {"senders": [], "domains": []},
            "always_junk": {"senders": [], "domains": ["doordash.com"]},
            "content_overrides": [],
            "conversation_threads": [],
        }
        with patch("gmail_strategy.load_rules", return_value=rules):
            result = gmail_strategy._classify_tier1(self._email(from_addr="noreply@doordash.com"))
        assert result is not None
        assert result.classification == "junk"

    def test_content_override_fraud_alert(self):
        rules = {
            "always_important": {"senders": [], "domains": []},
            "always_junk": {"senders": [], "domains": []},
            "content_overrides": [
                {"sender_pattern": "capital one", "content_pattern": "fraud|suspicious",
                 "classification": "urgent"},
            ],
            "conversation_threads": [],
        }
        with patch("gmail_strategy.load_rules", return_value=rules):
            result = gmail_strategy._classify_tier1(
                self._email(from_addr="alerts@capitalone.com",
                           from_name="Capital One",
                           subject="Suspicious activity on your account"))
        assert result is not None
        assert result.classification == "urgent"

    def test_content_override_payment_routine(self):
        rules = {
            "always_important": {"senders": [], "domains": []},
            "always_junk": {"senders": [], "domains": []},
            "content_overrides": [
                {"sender_pattern": "capital one", "content_pattern": "payment received",
                 "classification": "routine"},
            ],
            "conversation_threads": [],
        }
        with patch("gmail_strategy.load_rules", return_value=rules):
            result = gmail_strategy._classify_tier1(
                self._email(from_addr="alerts@capitalone.com",
                           from_name="Capital One",
                           subject="Payment received — thank you"))
        assert result is not None
        assert result.classification == "routine"

    def test_conversation_thread(self):
        rules = {
            "always_important": {"senders": [], "domains": []},
            "always_junk": {"senders": [], "domains": []},
            "content_overrides": [],
            "conversation_threads": ["thread_abc"],
        }
        with patch("gmail_strategy.load_rules", return_value=rules):
            result = gmail_strategy._classify_tier1(
                self._email(thread_id="thread_abc"))
        assert result is not None
        assert result.classification == "conversation"

    def test_no_match_returns_none(self):
        rules = gmail_strategy._default_rules()
        with patch("gmail_strategy.load_rules", return_value=rules), \
             patch("gmail_strategy._user_participated_in_thread", return_value=False):
            result = gmail_strategy._classify_tier1(self._email())
        assert result is None


class TestGlobalContentOverrides:
    """Global content overrides take priority over ALL sender/domain rules."""

    def _rules(self, **extra):
        base = {
            "always_important": {"senders": [], "domains": []},
            "always_junk": {"senders": [], "domains": ["junkdomain.com"]},
            "content_overrides": [],
            "conversation_threads": [],
            "global_content_overrides": [
                {
                    "content_pattern": r"verification code|one-time code|OTP|activate your|confirm your email|activation link|verify your",
                    "max_age_hours": 2,
                    "classification_within": "important",
                    "classification_after": "junk",
                },
            ],
        }
        base.update(extra)
        return base

    def _email(self, from_addr="noreply@junkdomain.com", subject="Test",
               body="", timestamp=None):
        return {
            "from_address": from_addr,
            "from_name": "",
            "subject": subject,
            "body": body,
            "thread_id": "t1",
            "labels": [],
            "gmail_category": None,
            "timestamp": timestamp,
        }

    def test_fresh_verification_code_overrides_junk_domain(self):
        """Email from always_junk domain with verification code, < 2 hours old."""
        ts = datetime.now(timezone.utc) - timedelta(minutes=30)
        email = self._email(
            from_addr="noreply@junkdomain.com",
            subject="Your verification code is 123456",
            timestamp=ts,
        )
        with patch("gmail_strategy.load_rules", return_value=self._rules()):
            result = gmail_strategy._classify_tier1(email)
        assert result is not None
        assert result.classification == "important"
        assert "Global content override" in result.reason

    def test_expired_verification_code_becomes_junk(self):
        """Email with verification code, > 2 hours old → junk."""
        ts = datetime.now(timezone.utc) - timedelta(hours=5)
        email = self._email(
            from_addr="noreply@junkdomain.com",
            subject="Your verification code is 123456",
            timestamp=ts,
        )
        with patch("gmail_strategy.load_rules", return_value=self._rules()):
            result = gmail_strategy._classify_tier1(email)
        assert result is not None
        assert result.classification == "junk"
        assert "expired" in result.reason

    def test_junk_domain_without_code_still_junk(self):
        """Email from always_junk domain with normal content → junk via sender rule."""
        ts = datetime.now(timezone.utc) - timedelta(minutes=30)
        email = self._email(
            from_addr="noreply@junkdomain.com",
            subject="50% off sale today!",
            timestamp=ts,
        )
        with patch("gmail_strategy.load_rules", return_value=self._rules()):
            result = gmail_strategy._classify_tier1(email)
        assert result is not None
        assert result.classification == "junk"
        assert "always_junk" in result.reason

    def test_activation_link_in_body(self):
        """Activation link detected in body, not just subject."""
        ts = datetime.now(timezone.utc) - timedelta(minutes=10)
        email = self._email(
            from_addr="noreply@junkdomain.com",
            subject="Welcome!",
            body="Click here to activate your account: https://example.com/activate",
            timestamp=ts,
        )
        with patch("gmail_strategy.load_rules", return_value=self._rules()):
            result = gmail_strategy._classify_tier1(email)
        assert result is not None
        assert result.classification == "important"

    def test_global_override_without_time_constraint(self):
        """Global override with no max_age_hours — always applies."""
        rules = self._rules()
        rules["global_content_overrides"].append({
            "content_pattern": r"security alert|account breach",
            "classification": "urgent",
        })
        email = self._email(
            from_addr="noreply@junkdomain.com",
            subject="Security alert: new sign-in detected",
            timestamp=datetime.now(timezone.utc) - timedelta(days=5),
        )
        with patch("gmail_strategy.load_rules", return_value=rules):
            result = gmail_strategy._classify_tier1(email)
        assert result is not None
        assert result.classification == "urgent"

    def test_no_global_overrides_section_graceful(self):
        """Rules file without global_content_overrides section doesn't crash."""
        rules = {
            "always_important": {"senders": [], "domains": []},
            "always_junk": {"senders": ["spam@test.com"], "domains": []},
            "content_overrides": [],
            "conversation_threads": [],
        }
        email = self._email(from_addr="spam@test.com", subject="Buy now")
        with patch("gmail_strategy.load_rules", return_value=rules):
            result = gmail_strategy._classify_tier1(email)
        assert result is not None
        assert result.classification == "junk"


class TestContentOverrideCategory:
    """Content overrides with category tag and optional content_pattern."""

    def _rules(self):
        return {
            "always_important": {"senders": [], "domains": []},
            "always_junk": {"senders": [], "domains": ["mercurycards.com"]},
            "content_overrides": [
                {
                    "sender_pattern": "mercurycards",
                    "content_pattern": "payment|transaction|statement",
                    "classification": "important",
                    "category": "Financial",
                },
                {
                    "sender_pattern": "usps",
                    "classification": "routine",
                    "category": "Physical Mail",
                },
            ],
            "conversation_threads": [],
            "global_content_overrides": [],
        }

    def test_content_override_with_category(self):
        """Mercury Card payment email → important + Financial category."""
        email = {
            "from_address": "no-reply@mercurycards.com",
            "from_name": "Mercury Card",
            "subject": "Payment confirmation",
            "body": "Your payment of $50 has been received.",
            "thread_id": "t1", "labels": [], "gmail_category": None,
        }
        with patch("gmail_strategy.load_rules", return_value=self._rules()):
            result = gmail_strategy._classify_tier1(email)
        assert result.classification == "important"
        assert result.category == "Financial"

    def test_content_override_no_match_falls_through(self):
        """Mercury Card ad (no payment keywords) → falls through to always_junk."""
        email = {
            "from_address": "no-reply@mercurycards.com",
            "from_name": "Mercury Card",
            "subject": "Earn rewards with Mercury!",
            "body": "Apply now for great benefits.",
            "thread_id": "t1", "labels": [], "gmail_category": None,
        }
        with patch("gmail_strategy.load_rules", return_value=self._rules()), \
             patch("gmail_strategy._user_participated_in_thread", return_value=False):
            result = gmail_strategy._classify_tier1(email)
        assert result.classification == "junk"

    def test_sender_only_override_no_content_pattern(self):
        """USPS with no content_pattern → matches all USPS emails."""
        email = {
            "from_address": "USPSInformeddelivery@email.informeddelivery.usps.com",
            "from_name": "USPS",
            "subject": "Your Daily Digest",
            "body": "You have mail arriving today.",
            "thread_id": "t1", "labels": [], "gmail_category": None,
        }
        with patch("gmail_strategy.load_rules", return_value=self._rules()):
            result = gmail_strategy._classify_tier1(email)
        assert result.classification == "routine"
        assert result.category == "Physical Mail"


class TestConversationTracking:
    """DB-backed conversation thread detection."""

    def test_user_replied_thread_is_conversation(self):
        rules = gmail_strategy._default_rules()
        email = {
            "from_address": "someone@example.com", "from_name": "",
            "subject": "Re: Hello", "body": "", "thread_id": "thread_xyz",
            "labels": [], "gmail_category": None,
        }
        with patch("gmail_strategy.load_rules", return_value=rules), \
             patch("gmail_strategy._user_participated_in_thread", return_value=True):
            result = gmail_strategy._classify_tier1(email)
        assert result.classification == "conversation"
        assert "user replied" in result.reason

    def test_no_reply_not_conversation(self):
        rules = gmail_strategy._default_rules()
        email = {
            "from_address": "someone@example.com", "from_name": "",
            "subject": "Hello", "body": "", "thread_id": "thread_xyz",
            "labels": [], "gmail_category": None,
        }
        with patch("gmail_strategy.load_rules", return_value=rules), \
             patch("gmail_strategy._user_participated_in_thread", return_value=False):
            result = gmail_strategy._classify_tier1(email)
        assert result is None


class TestAutoCleanup:
    """Auto-cleanup candidate detection."""

    def test_finds_expired_candidates(self):
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value.fetchall.return_value = [
            {"id": "msg1", "from_address": "x@shirtpunch.com", "subject": "Deal!"}
        ]
        rules = {
            **gmail_strategy._default_rules(),
            "auto_cleanup": [
                {"sender_pattern": "shirtpunch", "max_age_hours": 24, "action": "trash"}
            ],
        }
        with patch("gmail_strategy.load_rules", return_value=rules), \
             patch("db.get_conn", return_value=mock_conn):
            candidates = gmail_strategy.get_auto_cleanup_candidates()
        assert len(candidates) == 1
        assert candidates[0]["email_id"] == "msg1"
        assert candidates[0]["action"] == "trash"

    def test_no_rules_returns_empty(self):
        with patch("gmail_strategy.load_rules", return_value=gmail_strategy._default_rules()):
            candidates = gmail_strategy.get_auto_cleanup_candidates()
        assert candidates == []


class TestEmailWatches:
    """Email watch system — user-requested alerts that override sender rules."""

    def _email(self, from_addr="noreply@twilio.com", subject="Test",
               body="", from_name="Twilio"):
        return {
            "id": "msg_test_123",
            "from_address": from_addr,
            "from_name": from_name,
            "subject": subject,
            "body": body,
            "thread_id": "t1",
            "labels": [],
            "gmail_category": None,
        }

    def _watch(self, sender_pattern="twilio", content_pattern="refund",
               classification="urgent", description="Twilio refund"):
        return {
            "id": 1,
            "sender_pattern": sender_pattern,
            "content_pattern": content_pattern,
            "classification": classification,
            "description": description,
        }

    def test_watch_matches_and_overrides(self):
        """Email from junk sender matches active watch → classified per watch."""
        email = self._email(subject="Your refund has been processed")
        watches = [self._watch()]
        with patch("gmail_store.get_active_watches", return_value=watches), \
             patch("gmail_store.fulfill_watch") as mock_fulfill:
            result = gmail_strategy._check_email_watches(
                email, "noreply@twilio.com", f"{email['subject']} {email['body']}")
        assert result is not None
        assert result.classification == "urgent"
        assert "Twilio refund" in result.reason
        mock_fulfill.assert_called_once_with(1, "msg_test_123")

    def test_watch_no_content_match(self):
        """Email from watched sender but wrong content → no match."""
        email = self._email(subject="Your balance is low")
        watches = [self._watch()]
        with patch("gmail_store.get_active_watches", return_value=watches):
            result = gmail_strategy._check_email_watches(
                email, "noreply@twilio.com", f"{email['subject']} {email['body']}")
        assert result is None

    def test_watch_no_sender_match(self):
        """Email about refund but from wrong sender → no match."""
        email = self._email(from_addr="noreply@amazon.com", subject="Your refund",
                            from_name="Amazon")
        watches = [self._watch()]
        with patch("gmail_store.get_active_watches", return_value=watches):
            result = gmail_strategy._check_email_watches(
                email, "noreply@amazon.com", f"{email['subject']} {email['body']}")
        assert result is None

    def test_watch_sender_only(self):
        """Watch with only sender_pattern, no content_pattern → matches any content."""
        email = self._email(subject="Anything at all")
        watches = [self._watch(content_pattern="")]
        with patch("gmail_store.get_active_watches", return_value=watches), \
             patch("gmail_store.fulfill_watch"):
            result = gmail_strategy._check_email_watches(
                email, "noreply@twilio.com", f"{email['subject']} {email['body']}")
        assert result is not None
        assert result.classification == "urgent"

    def test_watch_content_only(self):
        """Watch with only content_pattern, no sender_pattern → matches any sender."""
        email = self._email(from_addr="anyone@anywhere.com", subject="Your refund is ready")
        watches = [self._watch(sender_pattern="")]
        with patch("gmail_store.get_active_watches", return_value=watches), \
             patch("gmail_store.fulfill_watch"):
            result = gmail_strategy._check_email_watches(
                email, "anyone@anywhere.com", f"{email['subject']} {email['body']}")
        assert result is not None

    def test_no_active_watches(self):
        """No active watches → no match."""
        email = self._email()
        with patch("gmail_store.get_active_watches", return_value=[]):
            result = gmail_strategy._check_email_watches(
                email, "noreply@twilio.com", f"{email['subject']}")
        assert result is None

    def test_watch_integrated_in_tier1(self):
        """Watch overrides always_junk in full classify_tier1 flow."""
        rules = gmail_strategy._default_rules()
        rules["always_junk"]["domains"].append("twilio.com")
        email = self._email(subject="Your refund has been processed")
        watches = [self._watch()]
        with patch("gmail_strategy.load_rules", return_value=rules), \
             patch("gmail_store.get_active_watches", return_value=watches), \
             patch("gmail_store.fulfill_watch"):
            result = gmail_strategy._classify_tier1(email)
        assert result is not None
        assert result.classification == "urgent"
        assert "watch" in result.reason.lower()


class TestTier2PatternScoring:
    def _email(self, **kwargs):
        base = {
            "from_address": "test@example.com",
            "from_name": "",
            "subject": "Normal email",
            "body": "Hello there",
            "thread_id": "t1",
            "labels": [],
            "gmail_category": None,
            "to_addresses": "",
            "data": {},
        }
        base.update(kwargs)
        return base

    def _patch_db(self):
        """Patch db.get_conn at module level (inline import in gmail_strategy)."""
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value.fetchone.return_value = None
        return patch("db.get_conn", return_value=mock_conn)

    def test_promotional_email_classified_junk(self):
        email = self._email(
            gmail_category="Promotions",
            subject="50% off sale — limited time!",
            from_address="deals@store.com",
        )
        with self._patch_db():
            result = gmail_strategy._classify_tier2(email)
        assert result is not None
        assert result.classification == "junk"

    def test_corporate_domain_positive_signal(self):
        email = self._email(from_address="boss@company.com")
        with self._patch_db():
            result = gmail_strategy._classify_tier2(email)
        # Corporate domain alone = +1, not enough for important
        assert result is not None
        assert result.classification == "routine"

    def test_freemail_promotions_junk(self):
        email = self._email(
            from_address="spammer@gmail.com",
            gmail_category="Promotions",
            subject="Buy now — unsubscribe",
        )
        with self._patch_db():
            result = gmail_strategy._classify_tier2(email)
        assert result is not None
        assert result.classification == "junk"


class TestTier3AIJudgment:
    def test_haiku_classifies(self):
        email = {
            "from_address": "unknown@somewhere.com",
            "from_name": "Unknown Sender",
            "subject": "Important notice",
            "snippet": "Please review the attached document",
        }
        # new=lambda forces a plain callable — patch() auto-detects async def
        # targets and creates AsyncMock even with return_value, which still
        # produces an unawaited coroutine when asyncio.run is also mocked.
        with patch("asyncio.get_running_loop", side_effect=RuntimeError), \
             patch("aria_api.ask_haiku", new=lambda *a, **kw: "important"), \
             patch("asyncio.run", return_value="important"):
            result = gmail_strategy._classify_tier3(email)
        assert result.classification == "important"
        assert result.tier == "tier3_ai"

    def test_haiku_failure_defaults_routine(self):
        email = {
            "from_address": "unknown@somewhere.com",
            "from_name": "",
            "subject": "Test",
            "snippet": "Test",
        }
        with patch("asyncio.get_running_loop", side_effect=RuntimeError), \
             patch("aria_api.ask_haiku", new=lambda *a, **kw: "routine"), \
             patch("asyncio.run", side_effect=Exception("API down")):
            result = gmail_strategy._classify_tier3(email)
        assert result.classification == "routine"


class TestClassifyEmail:
    def test_tier1_takes_priority(self):
        rules = {
            "always_important": {"senders": ["vip@test.com"], "domains": []},
            "always_junk": {"senders": [], "domains": []},
            "content_overrides": [],
            "conversation_threads": [],
        }
        email = {
            "from_address": "vip@test.com",
            "from_name": "",
            "subject": "Hello",
            "body": "",
            "thread_id": "t1",
            "labels": [],
            "gmail_category": "Promotions",  # would be junk in tier 2
        }
        with patch("gmail_strategy.load_rules", return_value=rules):
            result = gmail_strategy.classify_email(email)
        assert result.classification == "important"
        assert result.tier == "tier1_hard"


class TestClassifyBatch:
    def test_batch_classifies_all(self):
        emails = [
            {"from_address": "a@test.com", "from_name": "", "subject": "A",
             "body": "", "thread_id": "t1", "labels": [], "gmail_category": None,
             "to_addresses": "", "data": {}},
            {"from_address": "b@test.com", "from_name": "", "subject": "B",
             "body": "", "thread_id": "t2", "labels": [], "gmail_category": None,
             "to_addresses": "", "data": {}},
        ]
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value.fetchone.return_value = None
        with patch("gmail_strategy.load_rules", return_value=gmail_strategy._default_rules()), \
             patch("db.get_conn", return_value=mock_conn):
            results = gmail_strategy.classify_batch(emails)
        assert len(results) == 2
