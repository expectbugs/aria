"""Unicode, emoji, and special character tests through every data path.

Tests that multi-byte characters, emoji, accented text, and special punctuation
survive the full roundtrip through PostgreSQL and back.

SAFETY: Uses aria_test database (integration conftest). No production DB access.
"""

import json
from datetime import datetime, date
from unittest.mock import patch

import pytest

import calendar_store
import health_store
import nutrition_store
import vehicle_store
import legal_store
import timer_store
import actions
import db

from tests.integration.conftest import (
    seed_nutrition, seed_health, seed_fitbit_snapshot,
    seed_location, seed_timer, seed_reminder, seed_event,
    seed_legal, seed_vehicle, seed_request_log, seed_nudge_log,
)


def _action(payload: dict) -> str:
    return f"<!--ACTION::{json.dumps(payload, ensure_ascii=False)}-->"


# ---------------------------------------------------------------------------
# Roundtrip tests
# ---------------------------------------------------------------------------

class TestUnicodeRoundtrips:
    def test_nutrition_japanese_food_name(self):
        """Japanese food name survives DB roundtrip."""
        today = date.today().isoformat()
        seed_nutrition(day=today, food_name="\u9d8f\u8089\u306e\u7167\u308a\u713c\u304d",
                       calories=350, protein_g=30)
        items = nutrition_store.get_items(day=today)
        assert len(items) == 1
        assert items[0]["food_name"] == "\u9d8f\u8089\u306e\u7167\u308a\u713c\u304d"

    def test_event_emoji_title(self):
        """Event title with emoji survives DB roundtrip."""
        seed_event(title="Doctor \U0001f3e5 Appointment", event_date="2026-05-01")
        events = calendar_store.get_events(start="2026-05-01", end="2026-05-01")
        assert len(events) == 1
        assert "\U0001f3e5" in events[0]["title"]
        assert "Doctor" in events[0]["title"]

    def test_legal_accented_description(self):
        """Accented characters survive DB roundtrip."""
        today = date.today().isoformat()
        seed_legal(entry_date=today, entry_type="note",
                   description="Caf\u00e9 r\u00e9sum\u00e9")
        entries = legal_store.get_entries()
        assert len(entries) == 1
        assert entries[0]["description"] == "Caf\u00e9 r\u00e9sum\u00e9"

    def test_health_german_multibyte(self):
        """German multi-byte text doesn't crash pattern detection."""
        today = date.today().isoformat()
        seed_health(day=today, category="pain",
                    description="Schmerzen im R\u00fccken", severity=6)
        # Pattern detection should not crash
        patterns = health_store.get_patterns(days=7)
        assert isinstance(patterns, list)

    def test_reminder_emoji_text(self):
        """Emoji in reminder text survives roundtrip."""
        seed_reminder(text="\U0001f48a Take medication")
        reminders = calendar_store.get_reminders()
        assert len(reminders) == 1
        assert "\U0001f48a" in reminders[0]["text"]
        assert "Take medication" in reminders[0]["text"]

    def test_action_unicode_food_name(self):
        """ACTION block with unicode food name processes and stores correctly."""
        today = date.today().isoformat()
        resp = f"Logged! {_action({
            'action': 'log_nutrition',
            'food_name': '\u9d8f\u8089\u306e\u7167\u308a\u713c\u304d',
            'meal_type': 'dinner',
            'date': today,
            'nutrients': {'calories': 350, 'protein_g': 30},
        })}"
        cleaned = actions.process_actions_sync(resp)
        assert "Logged!" in cleaned

        items = nutrition_store.get_items(day=today)
        assert len(items) == 1
        assert items[0]["food_name"] == "\u9d8f\u8089\u306e\u7167\u308a\u713c\u304d"

    def test_vehicle_special_chars(self):
        """Apostrophes and dollar signs survive roundtrip."""
        today = date.today().isoformat()
        seed_vehicle(description="O'Reilly Auto $45.99", event_type="parts",
                     event_date=today, cost=45.99)
        entries = vehicle_store.get_entries()
        assert len(entries) == 1
        assert entries[0]["description"] == "O'Reilly Auto $45.99"

    def test_long_unicode_string_no_truncation(self):
        """10KB of mixed-script unicode stores and retrieves without truncation."""
        # Build a 10KB+ string from mixed scripts
        chars = (
            "\u3053\u3093\u306b\u3061\u306f"  # Japanese hiragana
            "\ud55c\uad6d\uc5b4"                # Korean
            "\u00e9\u00e8\u00ea\u00eb"          # French accents
            "\u0410\u0411\u0412\u0413"          # Cyrillic
            "\U0001f600\U0001f601\U0001f602"    # Emoji
            "ASCII-normal-text "
        )
        long_text = chars * 200  # Well over 10KB
        assert len(long_text.encode("utf-8")) > 10000

        today = date.today().isoformat()
        seed_health(day=today, category="general", description=long_text)
        entries = health_store.get_entries(days=1, category="general")
        assert len(entries) == 1
        assert entries[0]["description"] == long_text

    def test_nutrition_unicode_notes(self):
        """Unicode in nutrition notes field survives roundtrip."""
        today = date.today().isoformat()
        nutrition_store.add_item(
            food_name="Miso Soup",
            meal_type="dinner",
            nutrients={"calories": 50, "sodium_mg": 900},
            entry_date=today,
            notes="\u5473\u564c\u6c41 \u2014 homemade, low sodium variant",
        )
        items = nutrition_store.get_items(day=today)
        assert len(items) == 1
        assert "\u5473\u564c\u6c41" in items[0]["notes"]

    def test_timer_unicode_label_and_message(self):
        """Unicode in timer label and message survive roundtrip."""
        from datetime import timedelta
        fire_at = (datetime.now() + timedelta(hours=1)).isoformat()
        t = timer_store.add_timer(
            label="\u23f0 \u6642\u9593\u3067\u3059",
            fire_at=fire_at,
            message="\u304a\u85ac\u306e\u6642\u9593 \U0001f48a",
        )
        timer = timer_store.get_timer(t["id"])
        assert "\u23f0" in timer["label"]
        assert "\U0001f48a" in timer["message"]


# ===========================================================================
# Total: 10 tests
# ===========================================================================
