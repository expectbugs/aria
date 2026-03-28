"""Production nutrition data replay tests — real entries through validation, aggregation, and context.

Uses 56 real nutrition entries (March 17-26, 2026), 47 real health entries,
and 9 real Fitbit snapshots loaded from fixtures. Tests daily totals, JSONB
null handling, validation warnings, context generation, and weekly summaries
with actual production data shapes.

SAFETY: Uses aria_test database (integration conftest). No production DB access.
"""

import json
from datetime import datetime, date, timedelta
from unittest.mock import patch, MagicMock

import pytest
from freezegun import freeze_time

import nutrition_store
import health_store
import fitbit_store
import context
import actions
import db

from tests.integration.conftest import (
    load_fitbit_snapshots_into_db,
    load_nutrition_entries_into_db,
    load_health_entries_into_db,
    load_fixture,
    seed_fitbit_snapshot,
    seed_nutrition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def all_nutrition():
    """Load all 56 real nutrition entries into the test DB."""
    return load_nutrition_entries_into_db()


@pytest.fixture
def all_health():
    """Load all 47 real health entries into the test DB."""
    return load_health_entries_into_db()


@pytest.fixture
def all_fitbit():
    """Load all 9 real Fitbit snapshots into the test DB."""
    return load_fitbit_snapshots_into_db()


NUTRITION_DATES = [
    "2026-03-17", "2026-03-18", "2026-03-19", "2026-03-20", "2026-03-21",
    "2026-03-22", "2026-03-23", "2026-03-24", "2026-03-25", "2026-03-26",
]

# Expected entry counts per day from production data
EXPECTED_COUNTS = {
    "2026-03-17": 6, "2026-03-18": 6, "2026-03-19": 5, "2026-03-20": 5,
    "2026-03-21": 3, "2026-03-22": 6, "2026-03-23": 5, "2026-03-24": 6,
    "2026-03-25": 5, "2026-03-26": 9,
}


# ---------------------------------------------------------------------------
# Daily totals — basic aggregation
# ---------------------------------------------------------------------------

class TestDailyTotals:
    def test_all_days_have_items(self, all_nutrition):
        """Every day with nutrition data should show item_count > 0."""
        for day in NUTRITION_DATES:
            totals = nutrition_store.get_daily_totals(day)
            assert totals["item_count"] > 0, f"No items on {day}"

    def test_item_counts_match_fixture(self, all_nutrition):
        """Item counts should match the number of entries in the fixture."""
        for day, expected in EXPECTED_COUNTS.items():
            totals = nutrition_store.get_daily_totals(day)
            assert totals["item_count"] == expected, (
                f"Item count mismatch on {day}: {totals['item_count']} != {expected}"
            )

    def test_march_25_calories(self, all_nutrition):
        """March 25 daily total should be approximately 976 cal."""
        totals = nutrition_store.get_daily_totals("2026-03-25")
        # 100 + 156 + 280 + 0 + 440 = 976
        # Hard-boiled eggs: 78 cal * 2 servings = 156
        assert abs(totals["calories"] - 976) < 1, (
            f"March 25 calories: {totals['calories']} != 976"
        )

    def test_all_days_have_calories(self, all_nutrition):
        """Every day should have a positive calorie total."""
        for day in NUTRITION_DATES:
            totals = nutrition_store.get_daily_totals(day)
            assert totals["calories"] > 0, f"Zero calories on {day}"


# ---------------------------------------------------------------------------
# JSONB null handling — omega3 and choline edge cases
# ---------------------------------------------------------------------------

class TestJsonbNullHandling:
    def test_omega3_null_sums_to_zero(self, all_nutrition):
        """JSONB null omega3_mg in SQL SUM should return 0, not null or crash."""
        # March 25 has 5 entries, all with omega3_mg either null or missing
        totals = nutrition_store.get_daily_totals("2026-03-25")
        assert totals["omega3_mg"] == 0, (
            f"Expected omega3=0 on day with all nulls, got {totals['omega3_mg']}"
        )

    def test_choline_missing_key_sums_to_zero(self, all_nutrition):
        """Entries missing choline_mg key entirely should contribute 0 to SUM."""
        # Most entries on March 25 don't have a choline_mg key at all.
        # Only Hard-boiled eggs (2 servings) has choline_mg=147
        totals = nutrition_store.get_daily_totals("2026-03-25")
        # 147 * 2 servings = 294
        assert abs(totals["choline_mg"] - 294) < 1, (
            f"March 25 choline: {totals['choline_mg']} != 294"
        )

    def test_omega3_present_on_salmon_days(self, all_nutrition):
        """Days with salmon entries should have positive omega3 totals."""
        # March 17 has salmon dinner with omega3=2300
        totals = nutrition_store.get_daily_totals("2026-03-17")
        assert totals["omega3_mg"] > 0, (
            f"Expected positive omega3 on salmon day, got {totals['omega3_mg']}"
        )

    def test_null_vs_zero_in_nutrients(self, all_nutrition):
        """Verify that null nutrient values don't poison aggregation."""
        # March 17 has 6 entries with mixed null/0/present values
        totals = nutrition_store.get_daily_totals("2026-03-17")
        # iron_mg is null on all entries — should sum to 0
        assert totals["iron_mg"] == 0, (
            f"Expected iron=0 from all-null entries, got {totals['iron_mg']}"
        )


# ---------------------------------------------------------------------------
# Nutrition validation — egg choline detection
# ---------------------------------------------------------------------------

class TestValidateNutrition:
    def test_egg_choline_warning_on_combo_entry(self):
        """Combo entry 'Large coffee w/ nutpods + 2 hard-boiled eggs + smoothie'
        has cholesterol=372 but NO choline_mg key. _validate_nutrition should
        detect the egg keyword and warn about missing choline."""
        combo_action = {
            "food_name": "Large coffee w/ nutpods + 2 hard-boiled eggs + smoothie (1.5 scoops Huel)",
            "nutrients": {
                "calories": 591,
                "protein_g": 43.6,
                "cholesterol_mg": 372,
                # choline_mg intentionally absent (real data)
            },
            "source": "manual",
        }
        warnings = actions._validate_nutrition([combo_action], [])
        choline_warnings = [w for w in warnings if "choline" in w.lower() or "Choline" in w]
        # The _EGG_KEYWORDS regex matches 'eggs' in the combo name.
        # The validator should warn about missing choline.
        assert len(choline_warnings) > 0, (
            f"Expected choline warning for combo egg entry, got: {warnings}"
        )

    def test_standalone_egg_with_choline_no_warning(self):
        """Standalone 'Hard-boiled eggs' entry WITH choline should not warn."""
        egg_action = {
            "food_name": "Hard-boiled eggs",
            "nutrients": {
                "calories": 78,
                "cholesterol_mg": 186,
                "choline_mg": 147,  # present
                "protein_g": 6.3,
            },
            "source": "manual",
        }
        warnings = actions._validate_nutrition([egg_action], [])
        choline_warnings = [w for w in warnings if "choline" in w.lower() or "Choline" in w]
        assert len(choline_warnings) == 0, (
            f"Unexpected choline warning when choline is present: {warnings}"
        )

    def test_egg_low_cholesterol_warning(self):
        """Egg entry with suspiciously low cholesterol should warn."""
        bad_action = {
            "food_name": "Scrambled eggs",
            "nutrients": {
                "calories": 150,
                "cholesterol_mg": 50,  # too low for eggs
                "protein_g": 10,
            },
            "source": "estimate",
        }
        warnings = actions._validate_nutrition([bad_action], [])
        chol_warnings = [w for w in warnings if "cholesterol" in w.lower()]
        assert len(chol_warnings) > 0, (
            f"Expected cholesterol warning for low-chol eggs, got: {warnings}"
        )

    def test_salmon_omega3_warning(self):
        """Salmon entry without omega3 should warn."""
        salmon_action = {
            "food_name": "SafeCatch Wild Pink Salmon",
            "nutrients": {
                "calories": 180,
                "protein_g": 34,
                # omega3_mg intentionally absent
            },
            "source": "manual",
        }
        warnings = actions._validate_nutrition([salmon_action], [])
        omega_warnings = [w for w in warnings if "omega" in w.lower() or "Omega" in w]
        assert len(omega_warnings) > 0, (
            f"Expected omega-3 warning for salmon, got: {warnings}"
        )


# ---------------------------------------------------------------------------
# Context generation with real data
# ---------------------------------------------------------------------------

class TestNutritionContext:
    @freeze_time("2026-03-25 20:00:00")
    def test_context_has_calorie_totals(self, all_nutrition):
        """get_context on March 25 should include calorie totals."""
        result = nutrition_store.get_context("2026-03-25")
        assert isinstance(result, str)
        assert "976" in result, (
            f"Expected '976' calories in context, got:\n{result[:500]}"
        )

    @freeze_time("2026-03-25 20:00:00")
    def test_context_has_item_count(self, all_nutrition):
        """Context should mention the number of items logged."""
        result = nutrition_store.get_context("2026-03-25")
        assert "5 items" in result

    @freeze_time("2026-03-25 20:00:00")
    def test_context_has_sodium(self, all_nutrition):
        """Context should include sodium totals."""
        result = nutrition_store.get_context("2026-03-25")
        # March 25 sodium = 1264mg
        assert "1264" in result or "Sodium" in result

    @freeze_time("2026-03-25 20:00:00")
    def test_context_lists_food_items(self, all_nutrition):
        """Context should list individual food items."""
        result = nutrition_store.get_context("2026-03-25")
        # Should mention some of the actual foods
        assert "Factor Italian Pork Ragu" in result or "Pork Ragu" in result

    @freeze_time("2026-03-25 20:00:00")
    def test_context_choline_shown_when_present(self, all_nutrition):
        """Choline should appear in context when > 0 (eggs contribute 294mg)."""
        result = nutrition_store.get_context("2026-03-25")
        assert "Choline" in result or "choline" in result, (
            f"Expected choline mention in context:\n{result[:500]}"
        )

    def test_empty_day_returns_empty(self, all_nutrition):
        """A day with no entries should return empty string."""
        result = nutrition_store.get_context("2026-01-01")
        assert result == ""


# ---------------------------------------------------------------------------
# Weekly summary
# ---------------------------------------------------------------------------

class TestWeeklySummary:
    @freeze_time("2026-03-26 20:00:00")
    def test_weekly_summary_has_averages(self, all_nutrition):
        """Weekly summary should include average values."""
        result = nutrition_store.get_weekly_summary()
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Avg calories" in result
        assert "Avg protein" in result

    @freeze_time("2026-03-26 20:00:00")
    def test_weekly_summary_has_omega3_days(self, all_nutrition):
        """Weekly summary should report omega-3 days."""
        result = nutrition_store.get_weekly_summary()
        assert "Omega-3" in result or "omega-3" in result

    @freeze_time("2026-03-26 20:00:00")
    def test_weekly_summary_day_count(self, all_nutrition):
        """Weekly summary should report correct number of days logged."""
        result = nutrition_store.get_weekly_summary()
        # Last 7 days from March 26 = March 20-26
        # March 20, 21, 22, 23, 24, 25, 26 = 7 days with data
        assert "7 days logged" in result


# ---------------------------------------------------------------------------
# Check limits with real data
# ---------------------------------------------------------------------------

class TestCheckLimits:
    @freeze_time("2026-03-25 20:00:00")
    def test_check_limits_march_25(self, all_nutrition, all_fitbit):
        """March 25: 1264mg sodium should be within range (max 1800)."""
        warnings = nutrition_store.check_limits("2026-03-25")
        # sodium_mg=1264 is between min=1200 and max=1800, so no warning
        sodium_warnings = [w for w in warnings if "Sodium" in w and "above" in w]
        assert len(sodium_warnings) == 0, (
            f"Unexpected sodium warning at 1264mg: {warnings}"
        )

    @freeze_time("2026-03-25 20:00:00")
    def test_check_limits_returns_list(self, all_nutrition, all_fitbit):
        """check_limits should always return a list."""
        result = nutrition_store.check_limits("2026-03-25")
        assert isinstance(result, list)

    def test_check_limits_empty_day(self, all_nutrition):
        """No entries = no warnings."""
        result = nutrition_store.check_limits("2026-01-01")
        assert result == []


# ---------------------------------------------------------------------------
# Incomplete tracking — health vs nutrition count mismatch
# ---------------------------------------------------------------------------

class TestIncompleteTracking:
    @freeze_time("2026-03-25 20:00:00")
    def test_incomplete_tracking_warning(self, all_nutrition, all_health):
        """March 25: 9 health meals but only 5 nutrition items.
        gather_health_context should flag the mismatch."""
        result = context.gather_health_context()
        # The code checks diary_count > nutrition_count and warns
        assert "incomplete" in result.lower() or "only" in result.lower(), (
            f"Expected incomplete tracking warning in context:\n{result[:800]}"
        )

    @freeze_time("2026-03-25 20:00:00")
    def test_health_meal_count_march_25(self, all_health):
        """March 25 should have 9 health meal entries."""
        entries = health_store.get_entries(days=1, category="meal")
        march_25 = [e for e in entries if e.get("date") == "2026-03-25"]
        assert len(march_25) == 9


# ---------------------------------------------------------------------------
# get_items filtering
# ---------------------------------------------------------------------------

class TestGetItems:
    def test_filter_by_meal_type(self, all_nutrition):
        """Filtering by meal_type with real data should return correct items."""
        # March 25 has: breakfast, breakfast, breakfast, breakfast, lunch
        items = nutrition_store.get_items(day="2026-03-25", meal_type="lunch")
        assert len(items) >= 1
        for item in items:
            assert item["meal_type"] == "lunch"

    def test_filter_by_day(self, all_nutrition):
        """Filtering by day should return the right count."""
        items = nutrition_store.get_items(day="2026-03-25")
        assert len(items) == 5

    def test_filter_by_days_range(self, all_nutrition):
        """Filtering by days=3 should return multiple days of data."""
        items = nutrition_store.get_items(days=30)  # wide range to get all
        assert len(items) == 56


# ---------------------------------------------------------------------------
# Servings multiplier
# ---------------------------------------------------------------------------

class TestServingsMultiplier:
    def test_most_entries_have_servings_one(self, all_nutrition):
        """Most real entries have servings=1.0."""
        items = nutrition_store.get_items(days=30)
        servings_one = [i for i in items if i.get("servings") == 1.0]
        # At least 90% should be servings=1
        assert len(servings_one) >= 50, (
            f"Expected most entries to have servings=1.0, got {len(servings_one)}/56"
        )

    def test_eggs_have_servings_two(self, all_nutrition):
        """Hard-boiled eggs on March 25 have servings=2.0."""
        items = nutrition_store.get_items(day="2026-03-25")
        eggs = [i for i in items if "Hard-boiled eggs" in i.get("food_name", "")]
        assert len(eggs) == 1
        assert eggs[0]["servings"] == 2.0

    def test_servings_affects_calorie_total(self, all_nutrition):
        """Servings multiplier should be applied in daily totals."""
        # March 25: eggs are 78 cal * 2 servings = 156 cal
        totals = nutrition_store.get_daily_totals("2026-03-25")
        # Total = 100 + 156 + 280 + 0 + 440 = 976
        assert abs(totals["calories"] - 976) < 1


# ---------------------------------------------------------------------------
# Source types all aggregate correctly
# ---------------------------------------------------------------------------

class TestSourceTypes:
    def test_all_sources_present(self, all_nutrition):
        """Fixture has entries with source=estimate, label_photo, and manual."""
        items = nutrition_store.get_items(days=30)
        sources = set(i.get("source") for i in items)
        assert "manual" in sources
        assert "estimate" in sources
        assert "label_photo" in sources

    def test_all_sources_aggregate(self, all_nutrition):
        """Entries from all source types should contribute to daily totals."""
        # March 17 has manual (smoothie, coffee), estimate (teriyaki, broccoli,
        # salmon), and the early entries
        totals = nutrition_store.get_daily_totals("2026-03-17")
        assert totals["item_count"] == 6
        assert totals["calories"] > 0

    def test_label_photo_entries_exist(self, all_nutrition):
        """At least some entries should be from label_photo source."""
        items = nutrition_store.get_items(days=30)
        label_items = [i for i in items if i.get("source") == "label_photo"]
        assert len(label_items) == 10


# ---------------------------------------------------------------------------
# Net calories with combined real data
# ---------------------------------------------------------------------------

class TestNetCaloriesCombined:
    @freeze_time("2026-03-25 22:00:00")
    def test_net_calories_consumed(self, all_nutrition, all_fitbit):
        """Consumed calories should match nutrition totals."""
        result = nutrition_store.get_net_calories("2026-03-25")
        assert result["consumed"] == 976

    @freeze_time("2026-03-25 22:00:00")
    def test_net_calories_burned(self, all_nutrition, all_fitbit):
        """Burned calories should come from Fitbit activity data."""
        result = nutrition_store.get_net_calories("2026-03-25")
        # March 25 Fitbit caloriesOut = 1783
        assert result["burned"] == 1783

    @freeze_time("2026-03-25 22:00:00")
    def test_net_calorie_balance(self, all_nutrition, all_fitbit):
        """Net = consumed - burned."""
        result = nutrition_store.get_net_calories("2026-03-25")
        assert result["net"] == result["consumed"] - result["burned"]

    @freeze_time("2026-03-25 22:00:00")
    def test_on_track_for_deficit(self, all_nutrition, all_fitbit):
        """976 consumed - 1783 burned = -807 deficit. Target is 500+."""
        result = nutrition_store.get_net_calories("2026-03-25")
        assert result["on_track"] is True

    def test_no_fitbit_data_returns_zero_burned(self, all_nutrition):
        """Day with no Fitbit data should show burned=0."""
        result = nutrition_store.get_net_calories("2026-03-17")
        # March 17 has nutrition but no fitbit snapshot (snapshots start Mar 19)
        assert result["burned"] == 0
        assert result["on_track"] is None


# ---------------------------------------------------------------------------
# March 26 duplicate entries (re-logged dinner)
# ---------------------------------------------------------------------------

class TestDuplicateDay:
    def test_march_26_has_nine_entries(self, all_nutrition):
        """March 26 has 7 dinner + 2 snack entries (likely duplicates from re-logging)."""
        items = nutrition_store.get_items(day="2026-03-26")
        assert len(items) == 9

    def test_march_26_dinner_count(self, all_nutrition):
        """March 26 should have multiple dinner entries."""
        items = nutrition_store.get_items(day="2026-03-26", meal_type="dinner")
        assert len(items) >= 5, (
            f"Expected 5+ dinner entries on March 26, got {len(items)}"
        )
