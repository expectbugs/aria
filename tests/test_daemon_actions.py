"""Tests for actions.py — ACTION block processing.

SAFETY: All store operations are mocked. No real data is modified.
"""

import json
from unittest.mock import patch, MagicMock, AsyncMock

import actions


def _patch_cal():
    """Patch calendar_store with async-compatible mocks for write methods."""
    p = patch("actions.calendar_store")
    mock_cal = p.start()
    mock_cal.add_event = AsyncMock(return_value={"id": "abc12345", "title": "Test"})
    mock_cal.modify_event = AsyncMock(return_value={"id": "abc12345", "title": "Updated"})
    mock_cal.delete_event = AsyncMock(return_value=True)
    # Sync methods stay as regular MagicMock
    return mock_cal, p


class TestProcessActions:
    """Test the process_actions() function that extracts and executes ACTION blocks."""

    def test_add_event(self):
        mock_cal, p = _patch_cal()
        try:
            response = 'Got it! <!--ACTION::{"action": "add_event", "title": "Dentist", "date": "2026-03-20", "time": "14:30"}-->'
            result = actions.process_actions_sync(response)
            mock_cal.add_event.assert_called_once_with(
                title="Dentist", event_date="2026-03-20", time="14:30", notes=None,
            )
            assert "<!--ACTION" not in result
        finally:
            p.stop()

    @patch("actions.calendar_store")
    def test_add_reminder(self, mock_cal):
        response = 'Sure! <!--ACTION::{"action": "add_reminder", "text": "Buy milk", "due": "2026-03-21"}-->'
        actions.process_actions_sync(response)
        mock_cal.add_reminder.assert_called_once()

    @patch("actions.calendar_store")
    def test_add_reminder_with_location(self, mock_cal):
        response = 'Done! <!--ACTION::{"action": "add_reminder", "text": "Check mail", "location": "home", "location_trigger": "arrive"}-->'
        actions.process_actions_sync(response)
        mock_cal.add_reminder.assert_called_once_with(
            text="Check mail", due=None, recurring=None,
            location="home", location_trigger="arrive",
        )

    @patch("actions.calendar_store")
    def test_complete_reminder(self, mock_cal):
        mock_cal.complete_reminder.return_value = {"id": "abc"}
        response = 'Done! <!--ACTION::{"action": "complete_reminder", "id": "abc"}-->'
        result = actions.process_actions_sync(response)
        assert "failed" not in result.lower()

    @patch("actions.calendar_store")
    def test_complete_reminder_not_found(self, mock_cal):
        mock_cal.complete_reminder.return_value = None
        response = 'Done! <!--ACTION::{"action": "complete_reminder", "id": "bad"}-->'
        result = actions.process_actions_sync(response)
        assert "failed" in result.lower() or "couldn't" in result.lower()

    def test_modify_event(self):
        mock_cal, p = _patch_cal()
        try:
            response = 'Updated! <!--ACTION::{"action": "modify_event", "id": "abc", "title": "New Title"}-->'
            actions.process_actions_sync(response)
            mock_cal.modify_event.assert_called_once()
        finally:
            p.stop()

    def test_delete_event(self):
        mock_cal, p = _patch_cal()
        try:
            response = 'Deleted! <!--ACTION::{"action": "delete_event", "id": "abc"}-->'
            result = actions.process_actions_sync(response)
            assert "failed" not in result.lower()
        finally:
            p.stop()

    @patch("actions.vehicle_store")
    def test_log_vehicle(self, mock_vs):
        response = 'Logged! <!--ACTION::{"action": "log_vehicle", "date": "2026-03-15", "event_type": "oil_change", "description": "Full synthetic", "mileage": 145000}-->'
        actions.process_actions_sync(response)
        mock_vs.add_entry.assert_called_once()

    @patch("actions.health_store")
    def test_log_health(self, mock_hs):
        response = 'Noted! <!--ACTION::{"action": "log_health", "date": "2026-03-20", "category": "meal", "description": "grilled chicken", "meal_type": "lunch"}-->'
        actions.process_actions_sync(response)
        mock_hs.add_entry.assert_called_once()

    @patch("actions.legal_store")
    def test_log_legal(self, mock_ls):
        response = 'Logged! <!--ACTION::{"action": "log_legal", "date": "2026-03-20", "entry_type": "note", "description": "Filed motion"}-->'
        actions.process_actions_sync(response)
        mock_ls.add_entry.assert_called_once()

    @patch("actions.timer_store")
    def test_set_timer_relative(self, mock_ts):
        response = 'Timer set! <!--ACTION::{"action": "set_timer", "label": "Laundry", "minutes": 30, "delivery": "sms", "message": "Laundry done!"}-->'
        actions.process_actions_sync(response)
        mock_ts.add_timer.assert_called_once()
        kwargs = mock_ts.add_timer.call_args[1]
        assert kwargs["label"] == "Laundry"
        assert kwargs["delivery"] == "sms"

    @patch("actions.timer_store")
    def test_set_timer_absolute(self, mock_ts):
        response = 'Alarm set! <!--ACTION::{"action": "set_timer", "label": "Wake up", "time": "07:00", "delivery": "voice", "priority": "urgent"}-->'
        actions.process_actions_sync(response)
        mock_ts.add_timer.assert_called_once()

    @patch("actions.timer_store")
    def test_set_timer_missing_time_fields(self, mock_ts):
        response = 'Timer! <!--ACTION::{"action": "set_timer", "label": "Bad"}-->'
        result = actions.process_actions_sync(response)
        mock_ts.add_timer.assert_not_called()
        assert "needs" in result.lower() or "failed" in result.lower()

    @patch("actions.timer_store")
    def test_cancel_timer(self, mock_ts):
        mock_ts.cancel_timer.return_value = True
        response = 'Cancelled! <!--ACTION::{"action": "cancel_timer", "id": "tmr123"}-->'
        result = actions.process_actions_sync(response)
        assert "failed" not in result.lower()

    @patch("actions.nutrition_store")
    def test_log_nutrition(self, mock_ns):
        response = 'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Chicken", "meal_type": "lunch", "nutrients": {"calories": 250, "protein_g": 40}, "servings": 1.0}-->'
        actions.process_actions_sync(response)
        mock_ns.add_item.assert_called_once()

    @patch("actions._DESTRUCTIVE_ACTIONS", frozenset())
    @patch("actions.nutrition_store")
    def test_delete_nutrition(self, mock_ns):
        mock_ns.delete_item.return_value = True
        response = 'Deleted! <!--ACTION::{"action": "delete_nutrition_entry", "id": "nut123"}-->'
        actions.process_actions_sync(response)
        mock_ns.delete_item.assert_called_once_with("nut123")

    @patch("actions.fitbit_store")
    def test_start_exercise(self, mock_fs):
        response = 'Starting! <!--ACTION::{"action": "start_exercise", "exercise_type": "stationary_bike"}-->'
        actions.process_actions_sync(response)
        mock_fs.start_exercise.assert_called_once_with("stationary_bike")

    @patch("actions.fitbit_store")
    def test_end_exercise(self, mock_fs):
        response = 'Ending workout! <!--ACTION::{"action": "end_exercise"}-->'
        actions.process_actions_sync(response)
        mock_fs.end_exercise.assert_called_once_with("user ended")

    def test_set_delivery_voice(self):
        response = 'Answer via voice! <!--ACTION::{"action": "set_delivery", "method": "voice"}-->'
        meta = {}
        actions.process_actions_sync(response, metadata=meta)
        assert meta["delivery"] == "voice"

    def test_set_delivery_sms(self):
        response = 'Text answer! <!--ACTION::{"action": "set_delivery", "method": "sms"}-->'
        meta = {}
        actions.process_actions_sync(response, metadata=meta)
        assert meta["delivery"] == "sms"


class TestMultipleActions:
    @patch("actions.health_store")
    @patch("actions.nutrition_store")
    def test_multiple_actions_in_one_response(self, mock_ns, mock_hs):
        response = (
            'Logged your lunch! '
            '<!--ACTION::{"action": "log_nutrition", "food_name": "Salmon", "meal_type": "lunch", "nutrients": {"calories": 350}}-->'
            '<!--ACTION::{"action": "log_health", "date": "2026-03-20", "category": "meal", "description": "salmon", "meal_type": "lunch"}-->'
        )
        actions.process_actions_sync(response)
        mock_ns.add_item.assert_called_once()
        mock_hs.add_entry.assert_called_once()


class TestActionFailureHandling:
    def test_action_exception_appends_note(self):
        mock_cal, p = _patch_cal()
        mock_cal.add_event = AsyncMock(side_effect=Exception("DB error"))
        try:
            response = 'Done! <!--ACTION::{"action": "add_event", "title": "Test", "date": "2026-03-20"}-->'
            result = actions.process_actions_sync(response)
            assert "failed" in result.lower()
        finally:
            p.stop()

    def test_unknown_action_type_ignored(self):
        response = 'Test! <!--ACTION::{"action": "nonexistent_action", "data": "test"}-->'
        result = actions.process_actions_sync(response)
        # Should not crash, just log warning
        assert "<!--ACTION" not in result

    def test_malformed_json_in_action(self):
        response = 'Test! <!--ACTION::{"bad json}-->'
        result = actions.process_actions_sync(response)
        assert "failed" in result.lower()


class TestExpectActions:
    def test_missing_expected_actions(self):
        response = "Here are the nutrition details for that meal."
        result = actions.process_actions_sync(response, expect_actions=["log_nutrition"])
        assert "WARNING" in result
        assert "log_nutrition" in result

    @patch("actions.nutrition_store")
    def test_expected_actions_present(self, mock_ns):
        response = 'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Test", "nutrients": {}}-->'
        result = actions.process_actions_sync(response, expect_actions=["log_nutrition"])
        assert "WARNING" not in result


class TestClaimWithoutAction:
    def test_detects_claim_words(self):
        response = "I've logged your meal and tracked the calories."
        result = actions.process_actions_sync(response)
        assert "System note" in result
        assert "may not have been saved" in result

    def test_no_false_positive_on_clean_response(self):
        response = "The weather today is sunny and 55 degrees."
        result = actions.process_actions_sync(response)
        assert "System note" not in result

    def test_nutrition_extraction_without_action(self):
        response = (
            "I've logged that container — 450 calories, 38g protein, "
            "18g fat, 32g carbs, 680mg sodium, 6g fiber, "
            "2g added sugars, 95mg cholesterol, and 500mg potassium."
        )
        result = actions.process_actions_sync(response)
        assert "System note" in result


class TestActionBlockStripping:
    def test_strips_all_action_blocks(self):
        response = (
            "Message one! "
            '<!--ACTION::{"action": "add_event", "title": "A", "date": "2026-03-20"}-->'
            " Message two! "
            '<!--ACTION::{"action": "add_event", "title": "B", "date": "2026-03-21"}-->'
        )
        with patch("actions.calendar_store"):
            result = actions.process_actions_sync(response)
        assert "<!--ACTION" not in result
        assert "Message one!" in result
        assert "Message two!" in result


class TestNutritionValidation:
    """Tests for post-log nutrition validation checks."""

    @patch("actions.nutrition_store")
    def test_warns_missing_calories(self, mock_ns):
        response = (
            'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Mystery food", '
            '"nutrients": {"protein_g": 10}}-->'
        )
        result = actions.process_actions_sync(response)
        assert "Nutrition check" in result
        assert "No calories" in result

    @patch("actions.nutrition_store")
    def test_warns_zero_calories(self, mock_ns):
        response = (
            'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Water", '
            '"nutrients": {"calories": 0}}-->'
        )
        result = actions.process_actions_sync(response)
        assert "No calories" in result

    @patch("actions.nutrition_store")
    def test_no_warning_on_valid_entry(self, mock_ns):
        response = (
            'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Chicken breast", '
            '"meal_type": "lunch", "source": "estimate", '
            '"nutrients": {"calories": 250, "protein_g": 40, "total_fat_g": 5, '
            '"saturated_fat_g": 1, "sodium_mg": 300, "total_carb_g": 0, '
            '"dietary_fiber_g": 0, "total_sugars_g": 0, '
            '"choline_mg": 85, "magnesium_mg": 30}}-->'
        )
        result = actions.process_actions_sync(response)
        assert "Nutrition check" not in result

    @patch("actions.nutrition_store")
    def test_no_calorie_warning_on_supplements(self, mock_ns):
        """Supplements genuinely have 0 calories — don't warn."""
        for food in ["Nature Made Multi Complete multivitamin",
                     "Nature Made Magnesium Oxide supplement",
                     "Fish oil capsule", "Vitamin D3 tablet"]:
            response = (
                f'Logged! <!--ACTION::{{"action": "log_nutrition", "food_name": "{food}", '
                f'"nutrients": {{"calories": 0, "magnesium_mg": 200}}}}-->'
            )
            result = actions.process_actions_sync(response)
            assert "No calories" not in result, f"False warning on supplement: {food}"

    @patch("actions.nutrition_store")
    def test_warns_salmon_missing_omega3(self, mock_ns):
        response = (
            'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Canned salmon with rice", '
            '"nutrients": {"calories": 400, "protein_g": 30, "omega3_mg": null}}-->'
        )
        result = actions.process_actions_sync(response)
        assert "Omega-3 missing" in result
        assert "salmon" in result.lower()

    @patch("actions.nutrition_store")
    def test_no_omega3_warning_when_populated(self, mock_ns):
        response = (
            'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Salmon dinner", '
            '"nutrients": {"calories": 400, "protein_g": 30, "omega3_mg": 1150}}-->'
        )
        result = actions.process_actions_sync(response)
        assert "Omega-3" not in result

    @patch("actions.nutrition_store")
    def test_warns_fish_keyword_variants(self, mock_ns):
        for fish in ["tuna salad", "grilled fish tacos", "sardine snack"]:
            response = (
                f'Logged! <!--ACTION::{{"action": "log_nutrition", "food_name": "{fish}", '
                f'"nutrients": {{"calories": 200, "omega3_mg": null}}}}-->'
            )
            result = actions.process_actions_sync(response)
            assert "Omega-3 missing" in result, f"Failed for: {fish}"

    @patch("actions.nutrition_store")
    def test_warns_egg_low_cholesterol(self, mock_ns):
        response = (
            'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Scrambled eggs and toast", '
            '"nutrients": {"calories": 300, "cholesterol_mg": 30}}-->'
        )
        result = actions.process_actions_sync(response)
        assert "Cholesterol only 30mg" in result
        assert "186mg" in result

    @patch("actions.nutrition_store")
    def test_no_egg_warning_when_cholesterol_high(self, mock_ns):
        response = (
            'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Veggie omelet", '
            '"nutrients": {"calories": 400, "cholesterol_mg": 400}}-->'
        )
        result = actions.process_actions_sync(response)
        assert "Cholesterol only" not in result

    @patch("actions.nutrition_store")
    def test_no_egg_warning_for_eggplant(self, mock_ns):
        response = (
            'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Eggplant parmesan", '
            '"nutrients": {"calories": 350, "cholesterol_mg": 20}}-->'
        )
        result = actions.process_actions_sync(response)
        assert "Cholesterol only" not in result

    @patch("actions.nutrition_store")
    def test_warns_egg_missing_choline(self, mock_ns):
        response = (
            'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Scrambled eggs", '
            '"nutrients": {"calories": 300, "cholesterol_mg": 372, "protein_g": 25}}-->'
        )
        result = actions.process_actions_sync(response)
        assert "Choline missing" in result
        assert "147mg" in result

    @patch("actions.nutrition_store")
    def test_no_choline_warning_when_present(self, mock_ns):
        response = (
            'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Scrambled eggs", '
            '"nutrients": {"calories": 300, "cholesterol_mg": 372, "choline_mg": 294}}-->'
        )
        result = actions.process_actions_sync(response)
        assert "Choline missing" not in result

    @patch("actions.nutrition_store")
    def test_no_choline_warning_for_eggplant(self, mock_ns):
        response = (
            'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Eggplant parmesan", '
            '"nutrients": {"calories": 350}}-->'
        )
        result = actions.process_actions_sync(response)
        assert "Choline missing" not in result

    @patch("actions.nutrition_store")
    def test_warns_chicken_missing_choline(self, mock_ns):
        response = (
            'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Factor Queso Chicken", '
            '"nutrients": {"calories": 550, "protein_g": 40}}-->'
        )
        result = actions.process_actions_sync(response)
        assert "Choline missing" in result
        assert "chicken" in result.lower() or "85mg" in result

    @patch("actions.nutrition_store")
    def test_no_chicken_choline_warning_when_present(self, mock_ns):
        response = (
            'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Grilled chicken breast", '
            '"nutrients": {"calories": 280, "choline_mg": 85}}-->'
        )
        result = actions.process_actions_sync(response)
        assert "Choline missing" not in result or "chicken" not in result.lower()

    @patch("actions.nutrition_store")
    def test_no_chicken_warning_for_chickpea(self, mock_ns):
        response = (
            'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Chickpea curry", '
            '"nutrients": {"calories": 300}}-->'
        )
        result = actions.process_actions_sync(response)
        choline_warnings = [w for w in (result.warnings if hasattr(result, 'warnings') else [])
                            if "Choline" in w and "chicken" in w.lower()]
        assert len(choline_warnings) == 0

    @patch("actions.nutrition_store")
    def test_warns_meat_missing_magnesium(self, mock_ns):
        response = (
            'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Pork ragu with rice", '
            '"nutrients": {"calories": 440, "protein_g": 25}}-->'
        )
        result = actions.process_actions_sync(response)
        assert "Magnesium missing" in result

    @patch("actions.nutrition_store")
    def test_no_magnesium_warning_when_present(self, mock_ns):
        response = (
            'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Chicken and rice bowl", '
            '"nutrients": {"calories": 500, "magnesium_mg": 60}}-->'
        )
        result = actions.process_actions_sync(response)
        assert "Magnesium missing" not in result

    @patch("actions.nutrition_store")
    def test_warns_label_photo_incomplete(self, mock_ns):
        response = (
            'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Some product", '
            '"source": "label_photo", '
            '"nutrients": {"calories": 200, "protein_g": 10, "total_fat_g": 5}}-->'
        )
        result = actions.process_actions_sync(response)
        assert "Nutrition check" in result
        assert "3/8 core nutrients" in result

    @patch("actions.nutrition_store")
    def test_no_label_warning_for_estimates(self, mock_ns):
        """Estimates are expected to have fewer nutrients — no warning."""
        response = (
            'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Restaurant meal", '
            '"source": "estimate", '
            '"nutrients": {"calories": 500, "protein_g": 30}}-->'
        )
        result = actions.process_actions_sync(response)
        assert "core nutrients" not in result

    @patch("actions.nutrition_store")
    @patch("actions.health_store")
    def test_warns_meal_type_mismatch(self, mock_hs, mock_ns):
        response = (
            'Logged your lunch! '
            '<!--ACTION::{"action": "log_health", "date": "2026-03-20", "category": "meal", '
            '"description": "chicken", "meal_type": "lunch"}-->'
            '<!--ACTION::{"action": "log_nutrition", "food_name": "Chicken", '
            '"meal_type": "dinner", "nutrients": {"calories": 300}}-->'
        )
        result = actions.process_actions_sync(response)
        assert "Meal type mismatch" in result

    @patch("actions.nutrition_store")
    @patch("actions.health_store")
    def test_no_warning_when_meal_types_match(self, mock_hs, mock_ns):
        response = (
            'Logged your lunch! '
            '<!--ACTION::{"action": "log_health", "date": "2026-03-20", "category": "meal", '
            '"description": "chicken", "meal_type": "lunch"}-->'
            '<!--ACTION::{"action": "log_nutrition", "food_name": "Chicken", '
            '"meal_type": "lunch", "nutrients": {"calories": 300}}-->'
        )
        result = actions.process_actions_sync(response)
        assert "Meal type mismatch" not in result

    @patch("actions.nutrition_store")
    @patch("actions.health_store")
    def test_no_meal_type_check_without_health_entry(self, mock_hs, mock_ns):
        """No meal_type warning if only log_nutrition was emitted (no diary entry)."""
        response = (
            'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Snack", '
            '"meal_type": "snack", "nutrients": {"calories": 100}}-->'
        )
        result = actions.process_actions_sync(response)
        assert "Meal type mismatch" not in result


class TestIntraResponseDedup:
    """Test that duplicate ACTION blocks within one response are deduplicated."""

    @patch("actions.nutrition_store")
    def test_duplicate_nutrition_blocks_only_logged_once(self, mock_ns):
        """Two identical log_nutrition blocks → only one add_item call."""
        response = (
            'Logged! '
            '<!--ACTION::{"action": "log_nutrition", "date": "2026-03-26", '
            '"food_name": "Salmon", "meal_type": "dinner", "nutrients": {"calories": 200}}-->'
            '<!--ACTION::{"action": "log_nutrition", "date": "2026-03-26", '
            '"food_name": "Salmon", "meal_type": "dinner", "nutrients": {"calories": 200}}-->'
        )
        actions.process_actions_sync(response)
        assert mock_ns.add_item.call_count == 1

    @patch("actions.nutrition_store")
    def test_different_foods_not_deduped(self, mock_ns):
        """Different food names are NOT deduplicated."""
        response = (
            'Logged! '
            '<!--ACTION::{"action": "log_nutrition", "date": "2026-03-26", '
            '"food_name": "Salmon", "meal_type": "dinner", "nutrients": {"calories": 200}}-->'
            '<!--ACTION::{"action": "log_nutrition", "date": "2026-03-26", '
            '"food_name": "Rice", "meal_type": "dinner", "nutrients": {"calories": 150}}-->'
        )
        actions.process_actions_sync(response)
        assert mock_ns.add_item.call_count == 2


class TestDateCrossCheck:
    """Test that date mismatches between log_health and log_nutrition are caught."""

    @patch("push_image.push_image", return_value=True)  # prevent real phone push
    @patch("actions.nutrition_store")
    @patch("actions.health_store")
    def test_date_mismatch_aborts_actions(self, mock_hs, mock_ns, mock_push):
        """If log_health date differs from log_nutrition date for same meal, abort."""
        response = (
            'Logged! '
            '<!--ACTION::{"action": "log_health", "date": "2026-03-25", "category": "meal", '
            '"description": "salmon", "meal_type": "dinner"}-->'
            '<!--ACTION::{"action": "log_nutrition", "date": "2026-03-26", '
            '"food_name": "Salmon", "meal_type": "dinner", "nutrients": {"calories": 200}}-->'
        )
        result = actions.process_actions_sync(response)
        assert "DATA QUALITY ERROR" in result
        assert "Date mismatch" in result
        # Neither store should have been called
        mock_ns.add_item.assert_not_called()
        mock_hs.add_entry.assert_not_called()

    @patch("actions.nutrition_store")
    @patch("actions.health_store")
    def test_matching_dates_proceed_normally(self, mock_hs, mock_ns):
        """Same dates on health and nutrition → proceed normally."""
        response = (
            'Logged! '
            '<!--ACTION::{"action": "log_health", "date": "2026-03-26", "category": "meal", '
            '"description": "salmon dinner", "meal_type": "dinner"}-->'
            '<!--ACTION::{"action": "log_nutrition", "date": "2026-03-26", '
            '"food_name": "Salmon", "meal_type": "dinner", "nutrients": {"calories": 200}}-->'
        )
        result = actions.process_actions_sync(response)
        assert "DATA QUALITY ERROR" not in result
        mock_hs.add_entry.assert_called_once()
        mock_ns.add_item.assert_called_once()
