"""Stress and capacity edge case tests.

Tests that large data volumes, many ACTION blocks, large JSONB, and
high row counts don't cause crashes, timeouts, or regex catastrophic
backtracking.

SAFETY: Uses aria_test database (integration conftest). No production DB access.
"""

import json
import time
from datetime import datetime, date, timedelta
from unittest.mock import patch

import pytest

import context
import actions
import nutrition_store
import timer_store
import fitbit_store
import db

from tests.integration.conftest import (
    seed_nutrition, seed_health, seed_fitbit_snapshot,
    seed_location, seed_timer, seed_reminder, seed_event,
    seed_legal, seed_vehicle, seed_request_log, seed_nudge_log,
)


def _action(payload: dict) -> str:
    return f"<!--ACTION::{json.dumps(payload)}-->"


# ---------------------------------------------------------------------------
# Capacity tests
# ---------------------------------------------------------------------------

class TestCapacity:
    def test_100_active_timers_context_no_crash(self):
        """100 active timers should not crash gather_always_context()."""
        future = (datetime.now() + timedelta(hours=1)).isoformat()
        for i in range(100):
            seed_timer(label=f"Timer {i}", fire_at=future)

        with patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = context.gather_always_context()

        assert isinstance(result, str)
        assert "Active timers:" in result

    def test_50_active_reminders_no_crash(self):
        """50 active reminders should not crash gather_always_context()."""
        for i in range(50):
            seed_reminder(text=f"Reminder {i}")

        with patch("context.redis_client") as mock_redis:
            mock_redis.get_active_tasks.return_value = []
            mock_redis.format_task_status.return_value = ""
            result = context.gather_always_context()

        assert isinstance(result, str)
        assert "Active reminders:" in result

    def test_100_nutrition_entries_aggregation(self):
        """100 nutrition entries for one day aggregate correctly."""
        today = date.today().isoformat()
        total_cal = 0
        total_protein = 0
        for i in range(100):
            cal = 50 + (i % 10)
            prot = 5 + (i % 5)
            total_cal += cal
            total_protein += prot
            nutrition_store.add_item(
                food_name=f"Item {i}",
                meal_type="snack",
                nutrients={"calories": cal, "protein_g": prot},
                entry_date=today,
            )

        totals = nutrition_store.get_daily_totals(today)
        assert totals["item_count"] == 100
        assert abs(totals["calories"] - total_cal) < 0.5
        assert abs(totals["protein_g"] - total_protein) < 0.5

    def test_very_long_response_no_regex_backtrack(self):
        """50KB response through process_actions completes in < 2 seconds."""
        # Build a 50KB response with no ACTION blocks
        long_text = "A" * 50000
        start = time.monotonic()
        result = actions.process_actions_sync(long_text)
        elapsed = time.monotonic() - start

        assert elapsed < 2.0, f"process_actions took {elapsed:.2f}s on 50KB input"
        assert result == long_text

    def test_20_action_blocks_all_executed(self):
        """20 ACTION blocks in one response should all be extracted and executed."""
        today = date.today().isoformat()
        blocks = []
        for i in range(20):
            blocks.append(_action({
                "action": "log_nutrition",
                "food_name": f"Food Item {i}",
                "meal_type": "snack",
                "date": today,
                "nutrients": {"calories": 100 + i, "protein_g": 10 + i},
            }))
        response = "Here are all your items! " + " ".join(blocks)
        actions.process_actions_sync(response)

        items = nutrition_store.get_items(day=today)
        assert len(items) == 20

    def test_large_jsonb_stored_and_retrieved(self):
        """Large JSONB (10KB nutrients dict) stores and retrieves correctly."""
        today = date.today().isoformat()
        # Build a large nutrients dict with all NUTRIENT_FIELDS + extras
        big_nutrients = {}
        for field in nutrition_store.NUTRIENT_FIELDS:
            big_nutrients[field] = 42.5
        # Add extra padding to reach ~10KB
        for i in range(200):
            big_nutrients[f"custom_nutrient_{i}"] = 99.9

        json_size = len(json.dumps(big_nutrients).encode("utf-8"))
        assert json_size > 5000, f"JSONB only {json_size} bytes, expected >5000"

        nutrition_store.add_item(
            food_name="Large JSONB Test",
            meal_type="lunch",
            nutrients=big_nutrients,
            entry_date=today,
        )

        items = nutrition_store.get_items(day=today)
        assert len(items) == 1
        stored = items[0]["nutrients"]
        for field in nutrition_store.NUTRIENT_FIELDS:
            assert stored[field] == 42.5
        assert stored["custom_nutrient_100"] == 99.9

    def test_7_days_fitbit_snapshots_trend(self):
        """7 days of Fitbit snapshots produce a valid trend summary."""
        for i in range(7):
            d = (date.today() - timedelta(days=i)).isoformat()
            seed_fitbit_snapshot(d, {
                "heart_rate": {"value": {"restingHeartRate": 60 + i,
                                          "heartRateZones": []}},
                "sleep": {"sleep": [{"isMainSleep": True,
                                      "minutesAsleep": 400 + i * 10,
                                      "efficiency": 90,
                                      "levels": {"summary": {
                                          "deep": {"minutes": 60},
                                          "light": {"minutes": 200},
                                          "rem": {"minutes": 80},
                                          "wake": {"minutes": 40},
                                      }}}]},
                "activity": {"steps": 5000 + i * 500, "caloriesOut": 2000,
                             "activityCalories": 400,
                             "fairlyActiveMinutes": 20, "veryActiveMinutes": 10,
                             "sedentaryMinutes": 700, "floors": 5,
                             "distances": [{"activity": "total", "distance": 3.0}]},
            })

        trend = fitbit_store.get_trend(days=7)
        assert "Fitbit trends" in trend
        assert "Avg resting HR" in trend
        assert "Avg sleep" in trend
        assert "Avg steps" in trend

    def test_100_request_log_entries_today_requests(self):
        """100 request_log entries do not crash _get_today_requests."""
        for i in range(100):
            seed_request_log(f"Question {i}", response=f"Answer {i}")

        # Use context._get_today_requests() to test the real SQL
        requests = context._get_today_requests()
        assert len(requests) == 100


# ===========================================================================
# Total: 8 tests
# ===========================================================================
