"""Tests for daemon.py — ACTION block processing.

SAFETY: All store operations are mocked. No real data is modified.
"""

import json
from unittest.mock import patch, MagicMock

import daemon


class TestProcessActions:
    """Test the process_actions() function that extracts and executes ACTION blocks."""

    @patch("daemon.calendar_store")
    def test_add_event(self, mock_cal):
        response = 'Got it! <!--ACTION::{"action": "add_event", "title": "Dentist", "date": "2026-03-20", "time": "14:30"}-->'
        result = daemon.process_actions(response)
        mock_cal.add_event.assert_called_once_with(
            title="Dentist", event_date="2026-03-20", time="14:30", notes=None,
        )
        assert "<!--ACTION" not in result
        assert "Dentist" not in result or "Got it!" in result

    @patch("daemon.calendar_store")
    def test_add_reminder(self, mock_cal):
        response = 'Sure! <!--ACTION::{"action": "add_reminder", "text": "Buy milk", "due": "2026-03-21"}-->'
        daemon.process_actions(response)
        mock_cal.add_reminder.assert_called_once()

    @patch("daemon.calendar_store")
    def test_add_reminder_with_location(self, mock_cal):
        response = 'Done! <!--ACTION::{"action": "add_reminder", "text": "Check mail", "location": "home", "location_trigger": "arrive"}-->'
        daemon.process_actions(response)
        mock_cal.add_reminder.assert_called_once_with(
            text="Check mail", due=None, recurring=None,
            location="home", location_trigger="arrive",
        )

    @patch("daemon.calendar_store")
    def test_complete_reminder(self, mock_cal):
        mock_cal.complete_reminder.return_value = {"id": "abc"}
        response = 'Done! <!--ACTION::{"action": "complete_reminder", "id": "abc"}-->'
        result = daemon.process_actions(response)
        assert "failed" not in result.lower()

    @patch("daemon.calendar_store")
    def test_complete_reminder_not_found(self, mock_cal):
        mock_cal.complete_reminder.return_value = None
        response = 'Done! <!--ACTION::{"action": "complete_reminder", "id": "bad"}-->'
        result = daemon.process_actions(response)
        assert "failed" in result.lower() or "couldn't" in result.lower()

    @patch("daemon.calendar_store")
    def test_modify_event(self, mock_cal):
        mock_cal.modify_event.return_value = {"id": "abc", "title": "New Title"}
        response = 'Updated! <!--ACTION::{"action": "modify_event", "id": "abc", "title": "New Title"}-->'
        daemon.process_actions(response)
        mock_cal.modify_event.assert_called_once()

    @patch("daemon.calendar_store")
    def test_delete_event(self, mock_cal):
        mock_cal.delete_event.return_value = True
        response = 'Deleted! <!--ACTION::{"action": "delete_event", "id": "abc"}-->'
        result = daemon.process_actions(response)
        assert "failed" not in result.lower()

    @patch("daemon.vehicle_store")
    def test_log_vehicle(self, mock_vs):
        response = 'Logged! <!--ACTION::{"action": "log_vehicle", "date": "2026-03-15", "event_type": "oil_change", "description": "Full synthetic", "mileage": 145000}-->'
        daemon.process_actions(response)
        mock_vs.add_entry.assert_called_once()

    @patch("daemon.health_store")
    def test_log_health(self, mock_hs):
        response = 'Noted! <!--ACTION::{"action": "log_health", "date": "2026-03-20", "category": "meal", "description": "grilled chicken", "meal_type": "lunch"}-->'
        daemon.process_actions(response)
        mock_hs.add_entry.assert_called_once()

    @patch("daemon.legal_store")
    def test_log_legal(self, mock_ls):
        response = 'Logged! <!--ACTION::{"action": "log_legal", "date": "2026-03-20", "entry_type": "note", "description": "Filed motion"}-->'
        daemon.process_actions(response)
        mock_ls.add_entry.assert_called_once()

    @patch("daemon.timer_store")
    def test_set_timer_relative(self, mock_ts):
        response = 'Timer set! <!--ACTION::{"action": "set_timer", "label": "Laundry", "minutes": 30, "delivery": "sms", "message": "Laundry done!"}-->'
        daemon.process_actions(response)
        mock_ts.add_timer.assert_called_once()
        kwargs = mock_ts.add_timer.call_args[1]
        assert kwargs["label"] == "Laundry"
        assert kwargs["delivery"] == "sms"

    @patch("daemon.timer_store")
    def test_set_timer_absolute(self, mock_ts):
        response = 'Alarm set! <!--ACTION::{"action": "set_timer", "label": "Wake up", "time": "07:00", "delivery": "voice", "priority": "urgent"}-->'
        daemon.process_actions(response)
        mock_ts.add_timer.assert_called_once()

    @patch("daemon.timer_store")
    def test_set_timer_missing_time_fields(self, mock_ts):
        response = 'Timer! <!--ACTION::{"action": "set_timer", "label": "Bad"}-->'
        result = daemon.process_actions(response)
        mock_ts.add_timer.assert_not_called()
        assert "needs" in result.lower() or "failed" in result.lower()

    @patch("daemon.timer_store")
    def test_cancel_timer(self, mock_ts):
        mock_ts.cancel_timer.return_value = True
        response = 'Cancelled! <!--ACTION::{"action": "cancel_timer", "id": "tmr123"}-->'
        result = daemon.process_actions(response)
        assert "failed" not in result.lower()

    @patch("daemon.nutrition_store")
    def test_log_nutrition(self, mock_ns):
        response = 'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Chicken", "meal_type": "lunch", "nutrients": {"calories": 250, "protein_g": 40}, "servings": 1.0}-->'
        daemon.process_actions(response)
        mock_ns.add_item.assert_called_once()

    @patch("daemon.nutrition_store")
    def test_delete_nutrition(self, mock_ns):
        mock_ns.delete_item.return_value = True
        response = 'Deleted! <!--ACTION::{"action": "delete_nutrition_entry", "id": "nut123"}-->'
        daemon.process_actions(response)
        mock_ns.delete_item.assert_called_once_with("nut123")

    @patch("daemon.fitbit_store")
    def test_start_exercise(self, mock_fs):
        response = 'Starting! <!--ACTION::{"action": "start_exercise", "exercise_type": "stationary_bike"}-->'
        daemon.process_actions(response)
        mock_fs.start_exercise.assert_called_once_with("stationary_bike")

    @patch("daemon.fitbit_store")
    def test_end_exercise(self, mock_fs):
        response = 'Ending workout! <!--ACTION::{"action": "end_exercise"}-->'
        daemon.process_actions(response)
        mock_fs.end_exercise.assert_called_once_with("user ended")

    def test_set_delivery_voice(self):
        response = 'Answer via voice! <!--ACTION::{"action": "set_delivery", "method": "voice"}-->'
        meta = {}
        daemon.process_actions(response, metadata=meta)
        assert meta["delivery"] == "voice"

    def test_set_delivery_sms(self):
        response = 'Text answer! <!--ACTION::{"action": "set_delivery", "method": "sms"}-->'
        meta = {}
        daemon.process_actions(response, metadata=meta)
        assert meta["delivery"] == "sms"


class TestMultipleActions:
    @patch("daemon.health_store")
    @patch("daemon.nutrition_store")
    def test_multiple_actions_in_one_response(self, mock_ns, mock_hs):
        response = (
            'Logged your lunch! '
            '<!--ACTION::{"action": "log_nutrition", "food_name": "Salmon", "meal_type": "lunch", "nutrients": {"calories": 350}}-->'
            '<!--ACTION::{"action": "log_health", "date": "2026-03-20", "category": "meal", "description": "salmon", "meal_type": "lunch"}-->'
        )
        daemon.process_actions(response)
        mock_ns.add_item.assert_called_once()
        mock_hs.add_entry.assert_called_once()


class TestActionFailureHandling:
    @patch("daemon.calendar_store")
    @patch("daemon.log_request")
    def test_action_exception_appends_note(self, mock_log, mock_cal):
        mock_cal.add_event.side_effect = Exception("DB error")
        response = 'Done! <!--ACTION::{"action": "add_event", "title": "Test", "date": "2026-03-20"}-->'
        result = daemon.process_actions(response)
        assert "failed" in result.lower()

    def test_unknown_action_type_ignored(self):
        response = 'Test! <!--ACTION::{"action": "nonexistent_action", "data": "test"}-->'
        result = daemon.process_actions(response)
        # Should not crash, just log warning
        assert "<!--ACTION" not in result

    def test_malformed_json_in_action(self):
        response = 'Test! <!--ACTION::{"bad json}-->'
        result = daemon.process_actions(response)
        assert "failed" in result.lower()


class TestExpectActions:
    def test_missing_expected_actions(self):
        response = "Here are the nutrition details for that meal."
        result = daemon.process_actions(response, expect_actions=["log_nutrition"])
        assert "WARNING" in result
        assert "log_nutrition" in result

    @patch("daemon.nutrition_store")
    def test_expected_actions_present(self, mock_ns):
        response = 'Logged! <!--ACTION::{"action": "log_nutrition", "food_name": "Test", "nutrients": {}}-->'
        result = daemon.process_actions(response, expect_actions=["log_nutrition"])
        assert "WARNING" not in result


class TestClaimWithoutAction:
    def test_detects_claim_words(self):
        response = "I've logged your meal and tracked the calories."
        result = daemon.process_actions(response)
        assert "System note" in result
        assert "may not have been saved" in result

    def test_no_false_positive_on_clean_response(self):
        response = "The weather today is sunny and 55 degrees."
        result = daemon.process_actions(response)
        assert "System note" not in result

    def test_nutrition_extraction_without_action(self):
        response = (
            "I've logged that container — 450 calories, 38g protein, "
            "18g fat, 32g carbs, 680mg sodium, 6g fiber, "
            "2g added sugars, 95mg cholesterol, and 500mg potassium."
        )
        result = daemon.process_actions(response)
        assert "System note" in result


class TestActionBlockStripping:
    def test_strips_all_action_blocks(self):
        response = (
            "Message one! "
            '<!--ACTION::{"action": "add_event", "title": "A", "date": "2026-03-20"}-->'
            " Message two! "
            '<!--ACTION::{"action": "add_event", "title": "B", "date": "2026-03-21"}-->'
        )
        with patch("daemon.calendar_store"):
            result = daemon.process_actions(response)
        assert "<!--ACTION" not in result
        assert "Message one!" in result
        assert "Message two!" in result
