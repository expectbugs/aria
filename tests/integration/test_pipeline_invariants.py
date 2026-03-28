"""Integration tests: pipeline invariants that must ALWAYS hold.

Tests properties that must be true regardless of input, using real DB
and Hypothesis property-based testing where applicable.
"""

import json
import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from hypothesis import given, strategies as st, settings

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import actions
import context
import nutrition_store
import health_store
import calendar_store
import vehicle_store
import db
import training_store

from tests.integration.conftest import (
    seed_nutrition, seed_health, seed_fitbit_snapshot,
    seed_location, seed_timer, seed_reminder, seed_event,
)


# ---------------------------------------------------------------------------
# process_actions invariants (~8 tests)
# ---------------------------------------------------------------------------

class TestProcessActionsInvariants:
    """process_actions must never leave ACTION markers in output."""

    def test_process_actions_strips_all_markers_varied_inputs(self):
        """process_actions output NEVER contains <!--ACTION:: markers."""
        test_inputs = [
            "",
            "Hello, I set your timer.",
            "Here is the data.<!--ACTION::{\"action\":\"add_event\",\"title\":\"Test\",\"date\":\"2026-03-27\"}-->Done.",
            "No actions in this text at all.",
            'Unicode text: \u00e9\u00e8\u00ea\u00eb \u2603 \u2764',
            "Multiline\ntext\nwith\nno actions.",
            "Nested <!--ACTION::{\"action\":\"set_delivery\",\"method\":\"voice\"}--><!--ACTION::{\"action\":\"add_event\",\"title\":\"X\",\"date\":\"2026-03-27\"}-->end",
            '<!--ACTION::{"action":"log_health","date":"2026-03-27","category":"meal","description":"eggs"}-->Logged!',
            "Text before<!--ACTION::{\"action\":\"set_delivery\",\"method\":\"sms\"}-->text after",
            "Just text.",
            "Multiple\n\nblank lines\n\n\n",
            "Special chars: <>&\"' {} [] ()",
        ]
        for text in test_inputs:
            result = actions.process_actions_sync(text)
            assert "<!--ACTION::" not in result, (
                f"ACTION marker leaked for input: {text!r}"
            )

    def test_process_actions_empty_returns_empty(self):
        """process_actions('') returns ''."""
        result = actions.process_actions_sync("")
        assert result == ""

    def test_process_actions_no_actions_returns_same_text(self):
        """process_actions on text without actions returns it unchanged."""
        text = "no actions here"
        result = actions.process_actions_sync(text)
        assert result == text

    def test_process_actions_always_returns_str(self):
        """process_actions always returns str type."""
        inputs = ["", "hello", "x" * 10000, "\n\n\n", "\t\t"]
        for text in inputs:
            result = actions.process_actions_sync(text)
            assert isinstance(result.to_response(), str)

    @settings(max_examples=50, deadline=5000)
    @given(text=st.text(min_size=0, max_size=500))
    def test_hypothesis_process_actions_never_raises(self, text):
        """Hypothesis: given random text, process_actions never raises."""
        result = actions.process_actions_sync(text)
        assert isinstance(result.to_response(), str)

    @settings(max_examples=50, deadline=5000)
    @given(text=st.text(min_size=0, max_size=500))
    def test_hypothesis_process_actions_no_markers_in_output(self, text):
        """Hypothesis: given random text, output never contains ACTION markers."""
        result = actions.process_actions_sync(text)
        assert "<!--ACTION::" not in result

    def test_multiple_action_blocks_all_stripped(self):
        """Multiple ACTION blocks: all stripped, clean text preserved between."""
        text = (
            'Start text. '
            '<!--ACTION::{"action":"add_event","title":"A","date":"2026-03-27"}-->'
            ' Middle text. '
            '<!--ACTION::{"action":"add_event","title":"B","date":"2026-03-28"}-->'
            ' End text.'
        )
        result = actions.process_actions_sync(text)
        assert "<!--ACTION::" not in result
        assert "Start text." in result
        assert "Middle text." in result
        assert "End text." in result

    def test_action_block_at_start_and_end(self):
        """ACTION block at very start and very end of text both stripped."""
        text = (
            '<!--ACTION::{"action":"set_delivery","method":"voice"}-->'
            'Body text'
            '<!--ACTION::{"action":"add_event","title":"X","date":"2026-03-27"}-->'
        )
        result = actions.process_actions_sync(text)
        assert "<!--ACTION::" not in result
        assert "Body text" in result


# ---------------------------------------------------------------------------
# Context invariants (~5 tests)
# ---------------------------------------------------------------------------

class TestContextInvariants:
    """Context builders must always return str with expected properties."""

    def test_gather_always_context_returns_str(self):
        """gather_always_context() always returns str."""
        with patch("context.redis_client.get_active_tasks", return_value=[]), \
             patch("context.redis_client.format_task_status", return_value=""):
            result = context.gather_always_context()
        assert isinstance(result, str)

    def test_gather_always_context_contains_date(self):
        """gather_always_context() always contains a date string."""
        with patch("context.redis_client.get_active_tasks", return_value=[]), \
             patch("context.redis_client.format_task_status", return_value=""):
            result = context.gather_always_context()
        # Should contain the current date in human-readable format
        assert "Current date and time:" in result

    def test_gather_health_context_returns_str(self):
        """gather_health_context() returns str (never None)."""
        result = context.gather_health_context()
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_build_request_context_returns_str(self):
        """build_request_context('test') always returns str."""
        with patch("context.redis_client.get_active_tasks", return_value=[]), \
             patch("context.redis_client.format_task_status", return_value=""):
            result = await context.build_request_context("test")
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_context_length_under_200kb(self):
        """Context string length is always < 200KB even with seeded data."""
        today = date.today().isoformat()
        # Seed a moderate amount of data
        for i in range(10):
            seed_nutrition(today, f"Food item {i}", calories=200 + i * 10,
                           protein_g=20 + i)
            seed_health(today, category="meal", description=f"Meal {i}",
                        meal_type="lunch")
        seed_location("Home", lat=42.58, lon=-88.43)
        seed_timer(label="Test timer")
        seed_reminder(text="Test reminder")
        seed_event(title="Test event")
        seed_fitbit_snapshot(today, {
            "date": today,
            "activity": {"steps": 5000, "caloriesOut": 2000,
                         "activityCalories": 500, "sedentaryMinutes": 300,
                         "fairlyActiveMinutes": 20, "veryActiveMinutes": 10,
                         "floors": 5, "distances": []},
        })

        with patch("context.redis_client.get_active_tasks", return_value=[]), \
             patch("context.redis_client.format_task_status", return_value=""):
            result = await context.build_request_context("health diet nutrition")
        assert len(result) < 200 * 1024, f"Context too large: {len(result)} bytes"


# ---------------------------------------------------------------------------
# Store return type invariants (~6 tests)
# ---------------------------------------------------------------------------

class TestStoreReturnTypeInvariants:
    """Store functions must always return the documented types."""

    def test_health_store_get_entries_returns_list(self):
        """health_store.get_entries() always returns list."""
        result = health_store.get_entries()
        assert isinstance(result, list)

    def test_nutrition_store_get_items_returns_list(self):
        """nutrition_store.get_items() always returns list."""
        result = nutrition_store.get_items()
        assert isinstance(result, list)

    def test_nutrition_store_get_daily_totals_has_item_count(self):
        """nutrition_store.get_daily_totals() always returns dict with 'item_count'."""
        result = nutrition_store.get_daily_totals()
        assert isinstance(result, dict)
        assert "item_count" in result

    def test_calendar_store_get_events_returns_list(self):
        """calendar_store.get_events() always returns list."""
        result = calendar_store.get_events()
        assert isinstance(result, list)

    def test_calendar_store_get_reminders_returns_list(self):
        """calendar_store.get_reminders() always returns list."""
        result = calendar_store.get_reminders()
        assert isinstance(result, list)

    def test_vehicle_store_get_entries_returns_list(self):
        """vehicle_store.get_entries() always returns list."""
        result = vehicle_store.get_entries()
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Serialization invariants (~3 tests)
# ---------------------------------------------------------------------------

class TestSerializationInvariants:
    """db.serialize_row() must produce JSON-serializable output."""

    def test_serialize_row_date_to_str(self):
        """serialize_row() on a row with date converts to str."""
        row = {"id": 1, "date": date(2026, 3, 27)}
        result = db.serialize_row(row)
        assert isinstance(result["date"], str)
        assert result["date"] == "2026-03-27"

    def test_serialize_row_none_preserved(self):
        """serialize_row() on a row with None preserves None."""
        row = {"id": 1, "name": None, "value": 42}
        result = db.serialize_row(row)
        assert result["name"] is None
        assert result["value"] == 42

    def test_serialize_row_json_serializable(self):
        """serialize_row() output is JSON-serializable."""
        row = {
            "id": 1,
            "date": date(2026, 3, 27),
            "timestamp": datetime(2026, 3, 27, 14, 30, 0),
            "name": "test",
            "value": None,
            "count": 42,
            "ratio": 3.14,
        }
        result = db.serialize_row(row)
        # json.dumps must not crash
        serialized = json.dumps(result)
        assert isinstance(serialized, str)
        # Round-trip should preserve values
        deserialized = json.loads(serialized)
        assert deserialized["id"] == 1
        assert deserialized["name"] == "test"
        assert deserialized["value"] is None


# ---------------------------------------------------------------------------
# Training store invariants (~3 tests)
# ---------------------------------------------------------------------------

class TestTrainingStoreInvariants:
    """extract_entities() must always return list of tuples."""

    def test_extract_entities_returns_list_of_tuples(self):
        """extract_entities() always returns list of tuples."""
        result = training_store.extract_entities(
            "I talked to Dr. Smith about my pain", source="test"
        )
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, tuple)
            assert len(item) == 3

    def test_extract_entities_empty_returns_empty(self):
        """extract_entities('') returns []."""
        result = training_store.extract_entities("", source="test")
        assert result == []

    @settings(max_examples=50, deadline=5000)
    @given(text=st.text(min_size=0, max_size=500))
    def test_hypothesis_extract_entities_never_raises(self, text):
        """Hypothesis: given random text, extract_entities never raises."""
        result = training_store.extract_entities(text, source="test")
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, tuple)
            assert len(item) == 3
