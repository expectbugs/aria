"""Tests for system_prompt.py — verify prompt content, personality, and structural rules."""

import pytest
from unittest.mock import patch, MagicMock

# Mock config before importing system_prompt
_mock_config = MagicMock()
_mock_config.OWNER_NAME = "TestUser"
_mock_config.OWNER_LIVING_SITUATION = "Lives alone"
_mock_config.OWNER_WORK_SCHEDULE = "second shift"
_mock_config.OWNER_EMPLOYER = "TestCorp"
_mock_config.OWNER_WORK_STATUS = "active"
_mock_config.OWNER_VEHICLE = "2005 Nissan Xterra"
_mock_config.OWNER_HEALTH_NOTES = "NAFLD recovery"
_mock_config.OWNER_TIMEZONE = "America/Chicago"
_mock_config.KNOWN_PLACES = {"home": "123 Main St", "work": "456 Factory Rd"}


@pytest.fixture(autouse=True)
def mock_config(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "config", _mock_config)


def _get_primary_prompt():
    """Import and build primary prompt with mocked config."""
    import importlib
    import system_prompt
    importlib.reload(system_prompt)
    return system_prompt.build_primary_prompt()


# --- Personality ---

class TestPersonality:
    def test_snarky_default_mode(self):
        prompt = _get_primary_prompt()
        assert "snarky" in prompt.lower()
        assert "default mode" in prompt.lower()

    def test_smartass_identity(self):
        prompt = _get_primary_prompt()
        assert "smartass" in prompt.lower()

    def test_context_gates_present(self):
        """Serious mode for legal, health, emotional contexts."""
        prompt = _get_primary_prompt()
        assert "legal" in prompt.lower()
        assert "health concern" in prompt.lower()
        assert "emotional" in prompt.lower()

    def test_image_gen_humor_instruction(self):
        prompt = _get_primary_prompt()
        assert "image generation" in prompt.lower()
        assert "dispatch_action" in prompt
        assert "facial expression" in prompt.lower() or "reaction image" in prompt.lower()

    def test_no_trailing_questions(self):
        """Should instruct against 'would you like me to...' endings."""
        prompt = _get_primary_prompt()
        assert "would you like me to" in prompt.lower()  # the instruction mentions it to forbid it

    def test_contractions_and_casual(self):
        prompt = _get_primary_prompt()
        assert "contractions" in prompt.lower()
        assert "casual" in prompt.lower()


# --- Structural Rules ---

class TestStructuralRules:
    def test_integrity_rules_present(self):
        prompt = _get_primary_prompt()
        assert "ABSOLUTE RULES" in prompt
        assert "INTEGRITY" in prompt

    def test_action_block_templates(self):
        prompt = _get_primary_prompt()
        assert "<!--ACTION::" in prompt
        assert "add_event" in prompt
        assert "log_nutrition" in prompt
        assert "set_timer" in prompt

    def test_owner_name_substituted(self):
        prompt = _get_primary_prompt()
        assert "TestUser" in prompt

    def test_known_places_included(self):
        prompt = _get_primary_prompt()
        assert "home" in prompt
        assert "123 Main St" in prompt

    def test_delivery_routing_present(self):
        prompt = _get_primary_prompt()
        assert "set_delivery" in prompt

    def test_dispatch_action_present(self):
        prompt = _get_primary_prompt()
        assert "dispatch_action" in prompt

    def test_email_actions_present(self):
        prompt = _get_primary_prompt()
        assert "send_email" in prompt
        assert "watch_email" in prompt

    def test_exercise_actions_present(self):
        prompt = _get_primary_prompt()
        assert "start_exercise" in prompt
        assert "end_exercise" in prompt


# --- Other Prompt Builders ---

class TestActionPrompt:
    def test_action_prompt_is_worker(self):
        import importlib
        import system_prompt
        importlib.reload(system_prompt)
        prompt = system_prompt.build_action_prompt()
        assert "worker" in prompt.lower()
        assert "NOT conversational" in prompt

    def test_no_action_blocks_in_action_prompt(self):
        import importlib
        import system_prompt
        importlib.reload(system_prompt)
        prompt = system_prompt.build_action_prompt()
        assert "Do not emit ACTION blocks" in prompt


class TestAmnesiaPrompt:
    def test_amnesia_is_stateless(self):
        import importlib
        import system_prompt
        importlib.reload(system_prompt)
        prompt = system_prompt.build_amnesia_prompt()
        assert "stateless" in prompt.lower()

    def test_no_humor_instructions(self):
        """Amnesia prompt should not have humor/snarky personality instructions."""
        import importlib
        import system_prompt
        importlib.reload(system_prompt)
        prompt = system_prompt.build_amnesia_prompt()
        assert "snarky" not in prompt.lower()
        assert "smartass" not in prompt.lower()
        assert "humor" not in prompt.lower()
