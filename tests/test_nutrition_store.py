"""Tests for nutrition_store.py — nutrition tracking and analysis."""

from datetime import date, time, datetime, timedelta
from unittest.mock import patch, MagicMock

import nutrition_store
from helpers import make_nutrition_row

# Dynamic date for tests that go through add_item() validation (rejects >7 days old)
TODAY = date.today().isoformat()


def _patch_db():
    mock_conn = MagicMock()
    patcher = patch("nutrition_store.db.get_conn")
    mock_get_conn = patcher.start()
    mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn, patcher


class TestAddItem:
    def test_normal_item(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_nutrition_row()
        try:
            result = nutrition_store.add_item(
                food_name="Chicken breast", meal_type="lunch",
                nutrients={"calories": 250, "protein_g": 40},
                servings=1.0, serving_size="6 oz",
                entry_date=TODAY,
            )
            assert result["inserted"] is True
            assert result["entry"]["food_name"] == "Chicken breast"
            sql = mc.execute.call_args[0][0]
            assert "INSERT INTO nutrition_entries" in sql
        finally:
            p.stop()

    def test_zero_servings_raises_validation_error(self):
        """Zero servings is now a validation error instead of silent default."""
        import pytest
        with pytest.raises(ValueError, match="servings must be positive"):
            nutrition_store.add_item(
                food_name="Test", servings=0, entry_date=TODAY,
            )

    def test_negative_servings_raises_validation_error(self):
        """Negative servings is now a validation error."""
        import pytest
        with pytest.raises(ValueError, match="servings must be positive"):
            nutrition_store.add_item(
                food_name="Test", servings=-2, entry_date=TODAY,
            )

    def test_custom_date_and_time(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_nutrition_row()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        try:
            nutrition_store.add_item(
                food_name="Test", entry_date=yesterday,
                entry_time="08:30",
            )
            params = mc.execute.call_args[0][1]
            assert params[1] == yesterday  # date
            assert params[2] == "08:30"  # time
        finally:
            p.stop()

    def test_none_nutrients_becomes_empty_dict(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = make_nutrition_row(nutrients={})
        try:
            nutrition_store.add_item(food_name="Water", nutrients=None,
                                    entry_date=TODAY)
            # Should not raise
        finally:
            p.stop()

    def test_missing_date_raises_error(self):
        """entry_date is now required — no silent default to today."""
        import pytest
        with pytest.raises(ValueError, match="entry_date is required"):
            nutrition_store.add_item(food_name="Test", entry_date="")

    def test_duplicate_returns_not_inserted(self):
        """Content hash dedup: second identical insert returns inserted=False."""
        mc, p = _patch_db()
        mc.execute.return_value.fetchone.return_value = None  # ON CONFLICT DO NOTHING
        try:
            result = nutrition_store.add_item(
                food_name="Salmon", entry_date=TODAY,
            )
            assert result["inserted"] is False
            assert result["duplicate"] is True
        finally:
            p.stop()

    def test_future_date_raises_validation_error(self):
        """Dates in the future are rejected."""
        import pytest
        with pytest.raises(ValueError, match="future"):
            nutrition_store.add_item(food_name="Test", entry_date="2099-01-01")

    def test_absurd_calories_raises_validation_error(self):
        """Per-item calories > 5000 are rejected."""
        import pytest
        with pytest.raises(ValueError, match="calories"):
            nutrition_store.add_item(
                food_name="Test", entry_date=TODAY,
                nutrients={"calories": 9999},
            )


class TestDeleteItem:
    def test_success(self):
        mc, p = _patch_db()
        mc.execute.return_value.rowcount = 1
        try:
            assert nutrition_store.delete_item("nut12345") is True
        finally:
            p.stop()

    def test_not_found(self):
        mc, p = _patch_db()
        mc.execute.return_value.rowcount = 0
        try:
            assert nutrition_store.delete_item("bad") is False
        finally:
            p.stop()


class TestGetItems:
    def test_by_day(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = [make_nutrition_row()]
        try:
            items = nutrition_store.get_items(day="2026-03-20")
            sql = mc.execute.call_args[0][0]
            assert "date = %s" in sql
            assert len(items) == 1
        finally:
            p.stop()

    def test_by_days_range(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            nutrition_store.get_items(days=7)
            sql = mc.execute.call_args[0][0]
            assert "date >= %s" in sql
        finally:
            p.stop()

    def test_by_meal_type(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            nutrition_store.get_items(meal_type="lunch")
            sql = mc.execute.call_args[0][0]
            assert "meal_type = %s" in sql
        finally:
            p.stop()

    def test_no_filters(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            nutrition_store.get_items()
            sql = mc.execute.call_args[0][0]
            assert "WHERE" not in sql
        finally:
            p.stop()


class TestGetDailyTotals:
    def test_computes_totals(self):
        mc, p = _patch_db()
        # Simulate SQL SUM result
        row = {"item_count": 3}
        for field in nutrition_store.NUTRIENT_FIELDS:
            row[field] = 100.0
        mc.execute.return_value.fetchone.return_value = row
        try:
            totals = nutrition_store.get_daily_totals("2026-03-20")
            assert totals["item_count"] == 3
            assert totals["calories"] == 100.0
            assert totals["protein_g"] == 100.0
        finally:
            p.stop()

    def test_defaults_to_today(self):
        mc, p = _patch_db()
        row = {"item_count": 0}
        for field in nutrition_store.NUTRIENT_FIELDS:
            row[field] = 0.0
        mc.execute.return_value.fetchone.return_value = row
        try:
            nutrition_store.get_daily_totals()
            params = mc.execute.call_args[0][1]
            assert params[0] == date.today().isoformat()
        finally:
            p.stop()

    def test_rounds_values(self):
        mc, p = _patch_db()
        row = {"item_count": 1}
        for field in nutrition_store.NUTRIENT_FIELDS:
            row[field] = 33.333
        mc.execute.return_value.fetchone.return_value = row
        try:
            totals = nutrition_store.get_daily_totals("2026-03-20")
            for field in nutrition_store.NUTRIENT_FIELDS:
                assert totals[field] == 33.3
        finally:
            p.stop()


class TestGetNetCalories:
    @patch("nutrition_store.fitbit_store.get_activity_summary")
    @patch("nutrition_store.get_daily_totals")
    def test_computes_net_balance(self, mock_totals, mock_activity):
        mock_totals.return_value = {"calories": 1800}
        mock_activity.return_value = {"calories_total": 2500}
        result = nutrition_store.get_net_calories("2026-03-20")
        assert result["consumed"] == 1800
        assert result["burned"] == 2500
        assert result["net"] == -700
        assert result["on_track"] is True  # deficit >= 500

    @patch("nutrition_store.fitbit_store.get_activity_summary")
    @patch("nutrition_store.get_daily_totals")
    def test_surplus(self, mock_totals, mock_activity):
        mock_totals.return_value = {"calories": 3000}
        mock_activity.return_value = {"calories_total": 2500}
        result = nutrition_store.get_net_calories("2026-03-20")
        assert result["net"] == 500
        assert result["on_track"] is False

    @patch("nutrition_store.fitbit_store.get_activity_summary")
    @patch("nutrition_store.get_daily_totals")
    def test_no_fitbit_data(self, mock_totals, mock_activity):
        mock_totals.return_value = {"calories": 1800}
        mock_activity.return_value = None
        result = nutrition_store.get_net_calories("2026-03-20")
        assert result["burned"] == 0
        assert result["on_track"] is None


class TestCheckLimits:
    @patch("nutrition_store.get_net_calories")
    @patch("nutrition_store.get_daily_totals")
    def test_over_hard_limit(self, mock_totals, mock_net):
        mock_totals.return_value = {
            "item_count": 5, "calories": 2000, "protein_g": 50,
            "dietary_fiber_g": 10, "added_sugars_g": 40,
            "sodium_mg": 1000, "saturated_fat_g": 10, "total_sugars_g": 50,
        }
        mock_net.return_value = {"consumed": 2000, "burned": 0, "net": 2000}
        warnings = nutrition_store.check_limits("2026-03-20")
        assert any("OVER LIMIT" in w for w in warnings)

    @patch("nutrition_store.get_net_calories")
    @patch("nutrition_store.get_daily_totals")
    def test_warning_level(self, mock_totals, mock_net):
        mock_totals.return_value = {
            "item_count": 3, "calories": 1800, "protein_g": 110,
            "dietary_fiber_g": 30, "added_sugars_g": 28,
            "sodium_mg": 1500, "saturated_fat_g": 10, "total_sugars_g": 30,
        }
        mock_net.return_value = {"consumed": 1800, "burned": 0, "net": 1800}
        warnings = nutrition_store.check_limits("2026-03-20")
        assert any("WARNING" in w for w in warnings)

    @patch("nutrition_store.get_net_calories")
    @patch("nutrition_store.get_daily_totals")
    def test_positive_notes_fiber(self, mock_totals, mock_net):
        mock_totals.return_value = {
            "item_count": 3, "calories": 1700, "protein_g": 110,
            "dietary_fiber_g": 30, "added_sugars_g": 5,
            "sodium_mg": 1400, "saturated_fat_g": 10, "total_sugars_g": 15,
        }
        mock_net.return_value = {"consumed": 1700, "burned": 0, "net": 1700}
        warnings = nutrition_store.check_limits("2026-03-20")
        assert any("Fiber on track" in w for w in warnings)
        assert any("Protein on track" in w for w in warnings)

    @patch("nutrition_store.get_net_calories")
    @patch("nutrition_store.get_daily_totals")
    def test_no_items_returns_empty(self, mock_totals, mock_net):
        mock_totals.return_value = {"item_count": 0}
        assert nutrition_store.check_limits("2026-03-20") == []

    @patch("nutrition_store.get_net_calories")
    @patch("nutrition_store.get_daily_totals")
    def test_choline_positive_note(self, mock_totals, mock_net):
        mock_totals.return_value = {
            "item_count": 3, "calories": 1700, "protein_g": 80,
            "dietary_fiber_g": 15, "added_sugars_g": 5,
            "sodium_mg": 1400, "saturated_fat_g": 10, "total_sugars_g": 15,
            "choline_mg": 600,
        }
        mock_net.return_value = {"consumed": 1700, "burned": 0, "net": 1700}
        warnings = nutrition_store.check_limits("2026-03-20")
        assert any("Choline on track" in w for w in warnings)

    @patch("nutrition_store.get_net_calories")
    @patch("nutrition_store.get_daily_totals")
    def test_calorie_surplus_warning(self, mock_totals, mock_net):
        mock_totals.return_value = {
            "item_count": 3, "calories": 2500, "protein_g": 80,
            "dietary_fiber_g": 15, "added_sugars_g": 8,
            "sodium_mg": 1500, "saturated_fat_g": 12, "total_sugars_g": 20,
        }
        mock_net.return_value = {
            "consumed": 2500, "burned": 2000, "net": 500,
            "target_deficit_min": 500, "target_deficit_max": 1000,
        }
        warnings = nutrition_store.check_limits("2026-03-20")
        assert any("surplus" in w.lower() for w in warnings)


class TestGetContext:
    @patch("nutrition_store.check_limits")
    @patch("nutrition_store.get_net_calories")
    @patch("nutrition_store.get_items")
    @patch("nutrition_store.get_daily_totals")
    def test_builds_context_string(self, mock_totals, mock_items, mock_net, mock_limits):
        mock_totals.return_value = {
            "item_count": 2, "calories": 1200, "protein_g": 80,
            "total_fat_g": 30, "saturated_fat_g": 8, "trans_fat_g": 0,
            "cholesterol_mg": 150, "sodium_mg": 1000,
            "total_carb_g": 100, "dietary_fiber_g": 20,
            "total_sugars_g": 15, "added_sugars_g": 5,
            "vitamin_d_mcg": 0, "calcium_mg": 0, "iron_mg": 0,
            "potassium_mg": 0, "omega3_mg": 500,
        }
        mock_items.return_value = [
            make_nutrition_row(food_name="Oatmeal", meal_type="breakfast",
                             nutrients={"calories": 300}),
            make_nutrition_row(food_name="Chicken", meal_type="lunch",
                             nutrients={"calories": 400}),
        ]
        mock_net.return_value = {
            "consumed": 1200, "burned": 2000, "net": -800,
            "on_track": True,
        }
        mock_limits.return_value = ["Fiber on track: 20g"]

        ctx = nutrition_store.get_context("2026-03-20")
        assert "Nutrition today" in ctx
        assert "Oatmeal" in ctx
        assert "Chicken" in ctx
        assert "Calories:" in ctx
        assert "Omega-3:" in ctx
        assert "Fiber on track" in ctx

    @patch("nutrition_store.check_limits")
    @patch("nutrition_store.get_net_calories")
    @patch("nutrition_store.get_items")
    @patch("nutrition_store.get_daily_totals")
    def test_displays_micronutrients_when_nonzero(self, mock_totals, mock_items,
                                                   mock_net, mock_limits):
        mock_totals.return_value = {
            "item_count": 2, "calories": 1200, "protein_g": 80,
            "total_fat_g": 30, "saturated_fat_g": 8, "trans_fat_g": 0,
            "cholesterol_mg": 150, "sodium_mg": 1000,
            "total_carb_g": 100, "dietary_fiber_g": 20,
            "total_sugars_g": 15, "added_sugars_g": 5,
            "vitamin_d_mcg": 0, "calcium_mg": 0, "iron_mg": 0,
            "potassium_mg": 0, "omega3_mg": 0,
            "choline_mg": 294, "magnesium_mg": 200, "zinc_mg": 16,
            "vitamin_c_mg": 180, "selenium_mcg": 70, "vitamin_k_mcg": 430,
        }
        mock_items.return_value = []
        mock_net.return_value = {"consumed": 1200, "burned": 0, "net": 1200,
                                 "on_track": None}
        mock_limits.return_value = []

        ctx = nutrition_store.get_context("2026-03-20")
        assert "Choline: 294mg / 550mg target" in ctx
        assert "Magnesium: 200mg / 400-420mg" in ctx
        assert "Zinc: 16mg / 11mg" in ctx
        assert "Vitamin C: 180mg / 90mg" in ctx
        assert "Selenium: 70mcg / 55mcg" in ctx
        assert "Vitamin K: 430mcg / 120mcg" in ctx

    @patch("nutrition_store.get_daily_totals")
    def test_empty_returns_empty_string(self, mock_totals):
        mock_totals.return_value = {"item_count": 0}
        assert nutrition_store.get_context("2026-03-20") == ""


class TestGetWeeklySummary:
    def test_builds_summary(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = [
            {"date": date(2026, 3, 19), "item_count": 4,
             "calories": 1800.0, "protein_g": 110.0,
             "dietary_fiber_g": 28.0, "added_sugars_g": 8.0, "omega3_mg": 500.0,
             "choline_mg": 450.0, "magnesium_mg": 350.0},
            {"date": date(2026, 3, 20), "item_count": 3,
             "calories": 1700.0, "protein_g": 105.0,
             "dietary_fiber_g": 25.0, "added_sugars_g": 6.0, "omega3_mg": 0.0,
             "choline_mg": 294.0, "magnesium_mg": 200.0},
        ]
        try:
            summary = nutrition_store.get_weekly_summary()
            assert "2 days logged" in summary
            assert "Avg calories" in summary
            assert "Avg protein" in summary
            assert "Omega-3 days: 1/7" in summary
            assert "Avg choline" in summary
            assert "Avg magnesium" in summary
        finally:
            p.stop()

    def test_no_data(self):
        mc, p = _patch_db()
        mc.execute.return_value.fetchall.return_value = []
        try:
            assert nutrition_store.get_weekly_summary() == ""
        finally:
            p.stop()


class TestConstants:
    """Validate the nutrition constants are properly defined."""

    def test_daily_targets_have_required_keys(self):
        for nutrient, target in nutrition_store.DAILY_TARGETS.items():
            assert "min" in target
            assert "max" in target
            assert "unit" in target
            assert "label" in target

    def test_nutrient_fields_list(self):
        assert "calories" in nutrition_store.NUTRIENT_FIELDS
        assert "protein_g" in nutrition_store.NUTRIENT_FIELDS
        assert "omega3_mg" in nutrition_store.NUTRIENT_FIELDS
        assert "choline_mg" in nutrition_store.NUTRIENT_FIELDS
        assert "magnesium_mg" in nutrition_store.NUTRIENT_FIELDS
        assert len(nutrition_store.NUTRIENT_FIELDS) == 33

    def test_added_sugar_has_hard_limit(self):
        target = nutrition_store.DAILY_TARGETS["added_sugars_g"]
        assert target["hard_limit"] == 36
        assert target["warn"] == 25

    def test_micronutrient_daily_targets(self):
        for name in ["choline_mg", "magnesium_mg", "zinc_mg",
                      "vitamin_c_mg", "selenium_mcg"]:
            assert name in nutrition_store.DAILY_TARGETS
            t = nutrition_store.DAILY_TARGETS[name]
            assert "min" in t and "max" in t and "unit" in t and "label" in t
        assert len(nutrition_store.DAILY_TARGETS) == 12
