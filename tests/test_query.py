"""Tests for query.py — CLI data access helper.

SAFETY: All database access and stores mocked. No real PostgreSQL connections.
Verifies output format parity with aria_api._handle_tool_call().
"""

from unittest.mock import patch, MagicMock
from datetime import datetime, date, time

import pytest

import query


# ---------------------------------------------------------------------------
# format_health
# ---------------------------------------------------------------------------

class TestFormatHealth:
    def test_empty(self):
        assert query.format_health([]) == "No health entries found for the specified criteria."

    def test_basic_entry(self):
        entries = [{"id": "h1", "date": "2026-03-17", "category": "pain",
                    "description": "back pain", "severity": 8,
                    "sleep_hours": None, "meal_type": None}]
        result = query.format_health(entries)
        assert "Health log (1 entries):" in result
        assert "[id=h1]" in result
        assert "(severity 8/10)" in result

    def test_sleep_hours(self):
        entries = [{"id": "h2", "date": "2026-03-17", "category": "sleep",
                    "description": "restless", "severity": None,
                    "sleep_hours": 6.5, "meal_type": None}]
        result = query.format_health(entries)
        assert "(6.5h sleep)" in result

    def test_meal_type(self):
        entries = [{"id": "h3", "date": "2026-03-17", "category": "meal",
                    "description": "chicken", "severity": None,
                    "sleep_hours": None, "meal_type": "lunch"}]
        result = query.format_health(entries)
        assert "[lunch]" in result


# ---------------------------------------------------------------------------
# format_nutrition
# ---------------------------------------------------------------------------

class TestFormatNutrition:
    def test_empty(self):
        result = query.format_nutrition([], {"item_count": 0}, "2026-03-25")
        assert result == "No nutrition entries for 2026-03-25."

    def test_with_items(self):
        items = [{"id": "n1", "time": "12:30", "meal_type": "lunch",
                  "food_name": "Chicken", "servings": 1,
                  "nutrients": {"calories": 250}}]
        totals = {"item_count": 1, "calories": 250, "protein_g": 40,
                  "dietary_fiber_g": 3, "added_sugars_g": 0, "sodium_mg": 400,
                  "omega3_mg": 0, "choline_mg": 0, "magnesium_mg": 0,
                  "zinc_mg": 0, "vitamin_c_mg": 0, "selenium_mcg": 0,
                  "vitamin_k_mcg": 0}
        result = query.format_nutrition(items, totals, "2026-03-25")
        assert "Nutrition for 2026-03-25" in result
        assert "[id=n1]" in result
        assert "Calories: 250" in result
        assert "Protein: 40g" in result

    def test_multiple_servings(self):
        items = [{"id": "n2", "time": "08:00", "meal_type": "breakfast",
                  "food_name": "Eggs", "servings": 3,
                  "nutrients": {"calories": 90}}]
        totals = {"item_count": 1, "calories": 270, "protein_g": 18,
                  "dietary_fiber_g": 0, "added_sugars_g": 0, "sodium_mg": 210,
                  "omega3_mg": 0}
        result = query.format_nutrition(items, totals, "2026-03-25")
        assert "(3 servings)" in result
        assert "270 cal" in result

    def test_omega3_shown_when_positive(self):
        totals = {"item_count": 1, "calories": 300, "protein_g": 30,
                  "dietary_fiber_g": 0, "added_sugars_g": 0, "sodium_mg": 400,
                  "omega3_mg": 920}
        result = query.format_nutrition([], totals, "2026-03-25")
        assert "Omega-3: 920mg" in result


# ---------------------------------------------------------------------------
# format_vehicle
# ---------------------------------------------------------------------------

class TestFormatVehicle:
    def test_empty(self):
        assert query.format_vehicle([], {}) == "No vehicle maintenance entries found."

    def test_with_entries_and_latest(self):
        entries = [{"id": "v1", "date": "2026-03-15", "event_type": "oil_change",
                    "description": "Full synthetic", "mileage": 145000,
                    "cost": 45.99}]
        latest = {"oil_change": {"date": "2026-03-15", "mileage": 145000}}
        result = query.format_vehicle(entries, latest)
        assert "Vehicle log (1 entries):" in result
        assert "(145000 mi)" in result
        assert "($45.99)" in result
        assert "Latest per service type:" in result


# ---------------------------------------------------------------------------
# format_legal
# ---------------------------------------------------------------------------

class TestFormatLegal:
    def test_empty(self):
        assert query.format_legal([], []) == "No legal case entries found."

    def test_with_entries_and_upcoming(self):
        entries = [{"id": "l1", "date": "2026-03-18", "entry_type": "court_date",
                    "description": "Hearing"}]
        upcoming = [{"date": "2026-04-01", "description": "Next hearing"}]
        result = query.format_legal(entries, upcoming)
        assert "Legal case log (1 entries):" in result
        assert "[id=l1]" in result
        assert "Upcoming legal dates:" in result
        assert "Next hearing" in result


# ---------------------------------------------------------------------------
# format_calendar
# ---------------------------------------------------------------------------

class TestFormatCalendar:
    def test_empty(self):
        result = query.format_calendar([], "2026-03-25", "2026-04-01")
        assert "No calendar events between 2026-03-25 and 2026-04-01" in result

    def test_with_events(self):
        events = [{"id": "e1", "date": "2026-03-26", "title": "Dentist",
                   "time": "14:30"}]
        result = query.format_calendar(events, "2026-03-25", "2026-04-01")
        assert "Calendar events (2026-03-25 to 2026-04-01):" in result
        assert "[id=e1]" in result
        assert "at 14:30" in result

    def test_event_without_time(self):
        events = [{"id": "e2", "date": "2026-03-27", "title": "Birthday",
                   "time": None}]
        result = query.format_calendar(events, "2026-03-25", "2026-04-01")
        assert "Birthday" in result
        assert "at " not in result.split("Birthday")[1][:5]


# ---------------------------------------------------------------------------
# format_conversations
# ---------------------------------------------------------------------------

class TestFormatConversations:
    def test_empty(self):
        result = query.format_conversations([], 7, "")
        assert result == "No conversations found in the last 7 days."

    def test_empty_with_search(self):
        result = query.format_conversations([], 7, "salmon")
        assert "matching 'salmon'" in result

    def test_with_rows(self):
        rows = [{"timestamp": datetime(2026, 3, 27, 14, 30),
                 "input": "What did I eat?",
                 "response": "You had chicken for lunch."}]
        result = query.format_conversations(rows, 7, "")
        assert "Past conversations (1 found):" in result
        assert "What did I eat?" in result
        assert "You had chicken for lunch." in result


# ---------------------------------------------------------------------------
# cmd_* handlers via main()
# ---------------------------------------------------------------------------

class TestMainCLI:
    @patch("query.health_store")
    def test_health_command(self, mock_hs):
        mock_hs.get_entries.return_value = [
            {"id": "h1", "date": "2026-03-20", "category": "pain",
             "description": "back pain", "severity": 5,
             "sleep_hours": None, "meal_type": None}
        ]
        with patch("query._log_trace"):
            with patch("builtins.print") as mock_print:
                query.main(["health", "--days", "7", "--category", "pain"])
        output = mock_print.call_args[0][0]
        assert "back pain" in output
        mock_hs.get_entries.assert_called_once_with(days=7, category="pain")

    @patch("query.nutrition_store")
    def test_nutrition_command(self, mock_ns):
        mock_ns.get_items.return_value = []
        mock_ns.get_daily_totals.return_value = {"item_count": 0}
        with patch("query._log_trace"):
            with patch("builtins.print") as mock_print:
                query.main(["nutrition", "--date", "2026-03-25"])
        output = mock_print.call_args[0][0]
        assert "No nutrition entries" in output

    @patch("query.vehicle_store")
    def test_vehicle_command(self, mock_vs):
        mock_vs.get_entries.return_value = []
        mock_vs.get_latest_by_type.return_value = {}
        with patch("query._log_trace"):
            with patch("builtins.print") as mock_print:
                query.main(["vehicle", "--limit", "5"])
        mock_vs.get_entries.assert_called_once_with(limit=5)

    @patch("query.legal_store")
    def test_legal_command(self, mock_ls):
        mock_ls.get_entries.return_value = []
        mock_ls.get_upcoming_dates.return_value = []
        with patch("query._log_trace"):
            with patch("builtins.print") as mock_print:
                query.main(["legal"])
        mock_ls.get_entries.assert_called_once_with(limit=20)

    @patch("query.calendar_store")
    def test_calendar_command(self, mock_cs):
        mock_cs.get_events.return_value = []
        with patch("query._log_trace"):
            with patch("builtins.print") as mock_print:
                query.main(["calendar", "--start", "2026-03-25", "--end", "2026-04-01"])
        mock_cs.get_events.assert_called_once_with(
            start="2026-03-25", end="2026-04-01", owner="adam")

    @patch("query.db.get_conn")
    def test_conversations_command(self, mock_gc):
        mc = MagicMock()
        mc.execute.return_value.fetchall.return_value = []
        mock_gc.return_value.__enter__ = MagicMock(return_value=mc)
        mock_gc.return_value.__exit__ = MagicMock(return_value=False)
        with patch("query._log_trace"):
            with patch("builtins.print") as mock_print:
                query.main(["conversations", "--days", "3", "--search", "test"])
        output = mock_print.call_args[0][0]
        assert "No conversations" in output

    def test_missing_required_arg(self):
        with pytest.raises(SystemExit):
            query.main(["nutrition"])  # --date is required


# ---------------------------------------------------------------------------
# Output format parity with aria_api._handle_tool_call
# ---------------------------------------------------------------------------

class TestOutputFormatParity:
    """Verify query.py format functions produce identical output to aria_api."""

    def test_health_parity(self):
        """Identical to aria_api._handle_tool_call('query_health_log', {days: 7})."""
        entries = [
            {"id": "h1", "date": "2026-03-17", "category": "pain",
             "description": "back pain", "severity": 8,
             "sleep_hours": None, "meal_type": None},
        ]
        result = query.format_health(entries)
        # Must match: "Health log (1 entries):\n[id=h1] 2026-03-17 pain: back pain (severity 8/10)"
        assert result == (
            "Health log (1 entries):\n"
            "[id=h1] 2026-03-17 pain: back pain (severity 8/10)"
        )

    def test_calendar_parity(self):
        """Identical to aria_api._handle_tool_call('query_calendar', {...})."""
        events = [{"id": "e1", "date": "2026-03-26", "title": "Dentist",
                   "time": "14:30"}]
        result = query.format_calendar(events, "2026-03-25", "2026-04-01")
        assert result == (
            "Calendar events (2026-03-25 to 2026-04-01):\n"
            "  [id=e1] 2026-03-26 Dentist at 14:30"
        )

    def test_vehicle_parity(self):
        entries = [{"id": "v1", "date": "2026-03-15", "event_type": "oil_change",
                    "description": "Full synthetic", "mileage": 145000,
                    "cost": 45.99}]
        latest = {"oil_change": {"date": "2026-03-15", "mileage": 145000}}
        result = query.format_vehicle(entries, latest)
        assert "Vehicle log (1 entries):" in result
        assert "  [id=v1] 2026-03-15 oil_change: Full synthetic (145000 mi) ($45.99)" in result
        assert "Latest per service type:" in result
        assert "  oil_change: 2026-03-15 at 145000 mi" in result
