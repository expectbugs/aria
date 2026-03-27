"""Integration tests for nutrition_store — the most SQL-intensive module.

This is the highest-value integration test file because nutrition_store builds
dynamic SQL via f-strings from NUTRIENT_FIELDS. Only real PostgreSQL execution
can verify the generated COALESCE(SUM(CASE WHEN ...)) expressions are valid.
"""

from datetime import date, datetime

from unittest.mock import patch

import nutrition_store


class TestNutritionRoundtrip:
    def test_add_item_with_full_nutrients(self):
        today = date.today().isoformat()
        result = nutrition_store.add_item(
            food_name="Grilled Chicken",
            meal_type="lunch",
            nutrients={
                "calories": 350, "total_fat_g": 12, "saturated_fat_g": 3,
                "trans_fat_g": 0, "cholesterol_mg": 95, "sodium_mg": 400,
                "total_carb_g": 0, "dietary_fiber_g": 0, "total_sugars_g": 0,
                "added_sugars_g": 0, "protein_g": 52,
                "vitamin_d_mcg": None, "calcium_mg": None,
                "iron_mg": 2.0, "potassium_mg": 350, "omega3_mg": None,
                "choline_mg": 147, "magnesium_mg": 25, "zinc_mg": 2.5,
            },
            servings=1.0,
            serving_size="8 oz",
            source="manual",
            entry_date=today,
        )
        item = result["entry"]
        assert item["food_name"] == "Grilled Chicken"
        assert item["nutrients"]["calories"] == 350
        assert item["nutrients"]["protein_g"] == 52
        assert item["nutrients"]["vitamin_d_mcg"] is None  # NULL preserved
        assert item["nutrients"]["choline_mg"] == 147
        assert item["nutrients"]["magnesium_mg"] == 25

    def test_add_item_sparse_nutrients(self):
        """Items may have only a few known nutrients (e.g., estimates)."""
        today = date.today().isoformat()
        result = nutrition_store.add_item(
            food_name="Apple",
            nutrients={"calories": 95, "dietary_fiber_g": 4},
            entry_date=today,
        )
        item = result["entry"]
        assert item["nutrients"]["calories"] == 95
        assert "protein_g" not in item["nutrients"]

    def test_jsonb_roundtrip(self):
        """Verify JSONB column stores and retrieves complex dicts correctly."""
        today = date.today().isoformat()
        nutrients = {
            "calories": 450.5,
            "protein_g": 38,
            "omega3_mg": 1200,
            "sodium_mg": 680,
        }
        result = nutrition_store.add_item("Test Food", nutrients=nutrients,
                                          entry_date=today)
        item = result["entry"]
        items = nutrition_store.get_items(day=item["date"])
        retrieved = items[0]["nutrients"]
        assert retrieved["calories"] == 450.5
        assert retrieved["omega3_mg"] == 1200


class TestDailyTotalsSQL:
    """Test the dynamically-generated SQL aggregation with real data."""

    def test_single_item_totals(self):
        today = date.today().isoformat()
        nutrition_store.add_item(
            "Chicken", meal_type="lunch", entry_date=today,
            nutrients={"calories": 350, "protein_g": 40, "sodium_mg": 500},
            servings=1.0,
        )
        totals = nutrition_store.get_daily_totals(today)
        assert totals["item_count"] == 1
        assert totals["calories"] == 350.0
        assert totals["protein_g"] == 40.0
        assert totals["sodium_mg"] == 500.0
        # Fields not in the item should be 0 (COALESCE default)
        assert totals["omega3_mg"] == 0.0

    def test_multiple_items_sum(self):
        today = date.today().isoformat()
        nutrition_store.add_item(
            "Food A", entry_date=today,
            nutrients={"calories": 300, "protein_g": 25},
        )
        nutrition_store.add_item(
            "Food B", entry_date=today,
            nutrients={"calories": 200, "protein_g": 15, "sodium_mg": 400},
        )
        totals = nutrition_store.get_daily_totals(today)
        assert totals["item_count"] == 2
        assert totals["calories"] == 500.0
        assert totals["protein_g"] == 40.0
        assert totals["sodium_mg"] == 400.0  # only one item had sodium

    def test_servings_multiplier(self):
        today = date.today().isoformat()
        nutrition_store.add_item(
            "Rice", entry_date=today,
            nutrients={"calories": 200, "total_carb_g": 45},
            servings=2.5,
        )
        totals = nutrition_store.get_daily_totals(today)
        assert totals["calories"] == 500.0  # 200 * 2.5
        assert totals["total_carb_g"] == 112.5  # 45 * 2.5

    def test_null_nutrients_excluded_from_sum(self):
        """Nutrients with NULL values should NOT contribute 0 to sums."""
        today = date.today().isoformat()
        nutrition_store.add_item(
            "Item A", entry_date=today,
            nutrients={"calories": 100, "protein_g": None},
        )
        nutrition_store.add_item(
            "Item B", entry_date=today,
            nutrients={"calories": 200, "protein_g": 20},
        )
        totals = nutrition_store.get_daily_totals(today)
        # protein_g should be 20 (only item B), not 20+0
        assert totals["protein_g"] == 20.0
        assert totals["calories"] == 300.0

    def test_every_nutrient_field_is_valid_sql(self):
        """Ensure every field in NUTRIENT_FIELDS generates valid SQL."""
        today = date.today().isoformat()
        all_nutrients = {f: 1.0 for f in nutrition_store.NUTRIENT_FIELDS}
        nutrition_store.add_item(
            "Complete", entry_date=today, nutrients=all_nutrients,
        )
        totals = nutrition_store.get_daily_totals(today)
        for field in nutrition_store.NUTRIENT_FIELDS:
            assert field in totals
            assert totals[field] == 1.0

    def test_micronutrient_summing(self):
        """Verify new micronutrient fields aggregate correctly with null handling."""
        today = date.today().isoformat()
        nutrition_store.add_item(
            "Eggs", entry_date=today,
            nutrients={"calories": 156, "choline_mg": 294, "magnesium_mg": 10},
            servings=1.0,
        )
        nutrition_store.add_item(
            "Multivitamin", entry_date=today,
            nutrients={"calories": 0, "magnesium_mg": 100,
                       "zinc_mg": 15, "selenium_mcg": 70},
            servings=1.0,
        )
        totals = nutrition_store.get_daily_totals(today)
        assert totals["choline_mg"] == 294.0  # only eggs (multivitamin has no choline)
        assert totals["magnesium_mg"] == 110.0  # 10 + 100
        assert totals["zinc_mg"] == 15.0
        assert totals["selenium_mcg"] == 70.0

    def test_empty_day_returns_zeros(self):
        totals = nutrition_store.get_daily_totals("2099-01-01")
        assert totals["item_count"] == 0
        for field in nutrition_store.NUTRIENT_FIELDS:
            assert totals[field] == 0.0


class TestNetCalories:
    @patch("nutrition_store.fitbit_store.get_activity_summary")
    def test_with_fitbit_data(self, mock_activity):
        today = date.today().isoformat()
        nutrition_store.add_item(
            "Meal", entry_date=today,
            nutrients={"calories": 1800},
        )
        mock_activity.return_value = {"calories_total": 2500}

        net = nutrition_store.get_net_calories(today)
        assert net["consumed"] == 1800
        assert net["burned"] == 2500
        assert net["net"] == -700
        assert net["on_track"] is True


class TestCheckLimitsIntegration:
    @patch("nutrition_store.fitbit_store.get_activity_summary")
    def test_sugar_over_hard_limit(self, mock_activity):
        today = date.today().isoformat()
        nutrition_store.add_item(
            "Candy", entry_date=today,
            nutrients={"calories": 200, "added_sugars_g": 40, "total_sugars_g": 45},
        )
        mock_activity.return_value = None

        warnings = nutrition_store.check_limits(today)
        assert any("OVER LIMIT" in w for w in warnings)

    @patch("nutrition_store.fitbit_store.get_activity_summary")
    def test_positive_fiber_note(self, mock_activity):
        today = date.today().isoformat()
        nutrition_store.add_item(
            "Veggies", entry_date=today,
            nutrients={"calories": 200, "dietary_fiber_g": 30, "protein_g": 110},
        )
        mock_activity.return_value = None
        warnings = nutrition_store.check_limits(today)
        assert any("Fiber on track" in w for w in warnings)


class TestCheckLimitsCholine:
    @patch("nutrition_store.fitbit_store.get_activity_summary")
    def test_choline_positive_note(self, mock_activity):
        today = date.today().isoformat()
        nutrition_store.add_item(
            "Eggs", entry_date=today,
            nutrients={"calories": 156, "choline_mg": 294},
        )
        nutrition_store.add_item(
            "Broccoli", entry_date=today,
            nutrients={"calories": 105, "choline_mg": 57},
        )
        nutrition_store.add_item(
            "Multivitamin", entry_date=today,
            nutrients={"calories": 0},
        )
        nutrition_store.add_item(
            "Huel smoothie", entry_date=today,
            nutrients={"calories": 280, "choline_mg": 200},
        )
        mock_activity.return_value = None
        warnings = nutrition_store.check_limits(today)
        assert any("Choline on track" in w for w in warnings)


class TestContextIntegration:
    @patch("nutrition_store.fitbit_store.get_activity_summary")
    def test_context_string_with_real_data(self, mock_activity):
        today = date.today().isoformat()
        nutrition_store.add_item(
            "Breakfast Oatmeal", meal_type="breakfast", entry_date=today,
            nutrients={"calories": 300, "dietary_fiber_g": 8, "protein_g": 10},
        )
        nutrition_store.add_item(
            "Lunch Chicken", meal_type="lunch", entry_date=today,
            nutrients={"calories": 450, "protein_g": 45, "sodium_mg": 600},
        )
        mock_activity.return_value = None

        ctx = nutrition_store.get_context(today)
        assert "2 items logged" in ctx
        assert "Breakfast Oatmeal" in ctx
        assert "Lunch Chicken" in ctx
        assert "Calories:" in ctx
        assert "750" in ctx  # 300 + 450


class TestWeeklySummaryIntegration:
    def test_with_multi_day_data(self):
        for i in range(3):
            d = date.today().isoformat() if i == 0 else \
                (date.today() - __import__('datetime').timedelta(days=i)).isoformat()
            nutrition_store.add_item(
                f"Meal day {i}", entry_date=d,
                nutrients={"calories": 1800, "protein_g": 100,
                           "dietary_fiber_g": 25, "added_sugars_g": 8,
                           "choline_mg": 400, "magnesium_mg": 300},
            )

        summary = nutrition_store.get_weekly_summary()
        assert "3 days logged" in summary
        assert "Avg calories" in summary
        assert "Avg choline" in summary
        assert "Avg magnesium" in summary


class TestGetItems:
    def test_ordered_by_date_desc_time_desc(self):
        today = date.today().isoformat()
        nutrition_store.add_item("Early", entry_date=today, entry_time="08:00")
        nutrition_store.add_item("Late", entry_date=today, entry_time="20:00")

        items = nutrition_store.get_items(day=today)
        assert items[0]["food_name"] == "Late"  # newest first

    def test_filter_by_meal_type(self):
        today = date.today().isoformat()
        nutrition_store.add_item("Oats", meal_type="breakfast", entry_date=today)
        nutrition_store.add_item("Salad", meal_type="lunch", entry_date=today)

        lunches = nutrition_store.get_items(day=today, meal_type="lunch")
        assert len(lunches) == 1
        assert lunches[0]["food_name"] == "Salad"

    def test_delete_item(self):
        today = date.today().isoformat()
        result = nutrition_store.add_item("Delete me", entry_date=today)
        item = result["entry"]
        assert nutrition_store.delete_item(item["id"]) is True
        assert nutrition_store.get_items(day=item["date"]) == []
