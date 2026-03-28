"""Tests for gmail_strategy.py — 3-tier email classification engine."""

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
        with patch("gmail_strategy.load_rules", return_value=rules):
            result = gmail_strategy._classify_tier1(self._email())
        assert result is None


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
        with patch("asyncio.get_running_loop", side_effect=RuntimeError), \
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
