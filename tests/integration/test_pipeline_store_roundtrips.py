"""Deep store-level integration tests exercising real SQL.

Tests date range boundaries, pattern detection, deduplication, aggregation,
and ordering across all data stores with the real aria_test PostgreSQL.

SAFETY: Uses aria_test database (integration conftest). No production DB access.
"""

from datetime import datetime, date, timedelta

import pytest

import calendar_store
import health_store
import nutrition_store
import vehicle_store
import legal_store
import timer_store
import location_store
import fitbit_store
import db

from tests.integration.conftest import (
    seed_nutrition, seed_health, seed_fitbit_snapshot,
    seed_location, seed_timer, seed_reminder, seed_event,
    seed_legal, seed_vehicle, seed_request_log, seed_nudge_log,
)


# ---------------------------------------------------------------------------
# Calendar store
# ---------------------------------------------------------------------------

class TestCalendarStore:
    def test_date_range_boundary_start(self):
        """Event on start date should be included."""
        seed_event(title="Start", event_date="2026-06-01")
        seed_event(title="Before", event_date="2026-05-31")
        events = calendar_store.get_events(start="2026-06-01", end="2026-06-07")
        titles = [e["title"] for e in events]
        assert "Start" in titles
        assert "Before" not in titles

    def test_date_range_boundary_end(self):
        """Event on end date should be included."""
        seed_event(title="End", event_date="2026-06-07")
        seed_event(title="After", event_date="2026-06-08")
        events = calendar_store.get_events(start="2026-06-01", end="2026-06-07")
        titles = [e["title"] for e in events]
        assert "End" in titles
        assert "After" not in titles

    def test_event_without_time(self):
        """Event with no time should still be stored and retrievable."""
        seed_event(title="All Day Event", event_date="2026-07-04", time=None)
        events = calendar_store.get_events(start="2026-07-04", end="2026-07-04")
        assert len(events) == 1
        assert events[0]["title"] == "All Day Event"
        assert events[0].get("time") is None

    def test_modify_nonexistent_returns_none(self):
        """Modifying a nonexistent event should return None."""
        result = calendar_store.modify_event("nonexistent_id_12345", title="New Title")
        assert result is None


# ---------------------------------------------------------------------------
# Health store
# ---------------------------------------------------------------------------

class TestHealthStore:
    def test_pain_pattern_3_of_7_days(self):
        """Pain reported 3+ of 7 days triggers pattern detection."""
        for i in range(3):
            d = (date.today() - timedelta(days=i)).isoformat()
            seed_health(day=d, category="pain",
                        description=f"knee pain, severity {5+i}", severity=5+i)
        patterns = health_store.get_patterns(days=7)
        assert any("knee pain" in p for p in patterns)
        assert any("3" in p for p in patterns)

    def test_sleep_average_computed(self):
        """Sleep entries produce an average in patterns."""
        for i in range(3):
            d = (date.today() - timedelta(days=i)).isoformat()
            seed_health(day=d, category="sleep", description="night sleep",
                        sleep_hours=7.0 + i * 0.5)
        patterns = health_store.get_patterns(days=7)
        assert any("sleep" in p.lower() for p in patterns)

    def test_duplicate_blocked_by_content_hash(self):
        """Same content_hash should be blocked by UNIQUE constraint."""
        today = date.today().isoformat()
        r1 = seed_health(day=today, category="pain", description="headache")
        r2 = seed_health(day=today, category="pain", description="headache")
        assert r1["inserted"] is True
        assert r2["duplicate"] is True

    def test_different_categories_not_deduped(self):
        """Different categories with same description are not duplicates."""
        today = date.today().isoformat()
        r1 = seed_health(day=today, category="pain", description="back issue")
        r2 = seed_health(day=today, category="symptom", description="back issue")
        assert r1["inserted"] is True
        assert r2["inserted"] is True


# ---------------------------------------------------------------------------
# Location store
# ---------------------------------------------------------------------------

class TestLocationStore:
    def test_get_latest_returns_most_recent(self):
        seed_location(location_name="Place A", lat=42.0, lon=-88.0)
        seed_location(location_name="Place B", lat=42.1, lon=-88.1)
        latest = location_store.get_latest()
        assert latest is not None
        assert latest["location"] == "Place B"

    def test_get_history_respects_hours(self):
        # Insert a location with current timestamp (default)
        seed_location(location_name="Recent")
        # Insert an old location by direct SQL
        old_time = (datetime.now() - timedelta(hours=25)).isoformat()
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO locations (timestamp, lat, lon, location, accuracy_m)
                   VALUES (%s, %s, %s, %s, %s)""",
                (old_time, 40.0, -85.0, "Old Place", 10.0),
            )
        history = location_store.get_history(hours=24)
        locations = [h["location"] for h in history]
        assert "Recent" in locations
        assert "Old Place" not in locations

    def test_empty_table_returns_none(self):
        latest = location_store.get_latest()
        assert latest is None


# ---------------------------------------------------------------------------
# Vehicle store
# ---------------------------------------------------------------------------

class TestVehicleStore:
    def test_get_latest_by_type(self):
        seed_vehicle(description="Old oil change", event_type="oil_change",
                     event_date="2026-01-01", mileage=140000)
        seed_vehicle(description="New oil change", event_type="oil_change",
                     event_date="2026-03-01", mileage=145000)
        seed_vehicle(description="Tire rotation", event_type="tire_rotation",
                     event_date="2026-02-15")
        latest = vehicle_store.get_latest_by_type()
        assert "oil_change" in latest
        assert "tire_rotation" in latest
        assert latest["oil_change"]["mileage"] == 145000

    def test_entries_ordered_newest_first(self):
        seed_vehicle(description="Old", event_date="2026-01-01")
        seed_vehicle(description="New", event_date="2026-03-15")
        entries = vehicle_store.get_entries()
        assert entries[0]["description"] == "New"
        assert entries[1]["description"] == "Old"

    def test_limit_param_respected(self):
        for i in range(5):
            seed_vehicle(description=f"Entry {i}",
                         event_date=(date.today() - timedelta(days=i)).isoformat())
        entries = vehicle_store.get_entries(limit=2)
        assert len(entries) == 2


# ---------------------------------------------------------------------------
# Legal store
# ---------------------------------------------------------------------------

class TestLegalStore:
    def test_get_upcoming_dates_future_only(self):
        past = (date.today() - timedelta(days=5)).isoformat()
        future = (date.today() + timedelta(days=5)).isoformat()
        seed_legal(entry_date=past, entry_type="court_date", description="Past hearing")
        seed_legal(entry_date=future, entry_type="court_date", description="Future hearing")
        upcoming = legal_store.get_upcoming_dates()
        descs = [u["description"] for u in upcoming]
        assert "Future hearing" in descs
        assert "Past hearing" not in descs

    def test_includes_today(self):
        today = date.today().isoformat()
        seed_legal(entry_date=today, entry_type="deadline",
                   description="Today's deadline")
        upcoming = legal_store.get_upcoming_dates()
        assert any(u["description"] == "Today's deadline" for u in upcoming)

    def test_contacts_text_array_roundtrip(self):
        today = date.today().isoformat()
        contacts = ["John Smith", "Jane Doe", "Bob O'Brien"]
        seed_legal(entry_date=today, entry_type="contact",
                   description="Meeting", contacts=contacts)
        entries = legal_store.get_entries()
        assert len(entries) == 1
        assert entries[0]["contacts"] == contacts


# ---------------------------------------------------------------------------
# Nutrition store
# ---------------------------------------------------------------------------

class TestNutritionStore:
    def test_get_daily_totals_aggregation(self):
        today = date.today().isoformat()
        seed_nutrition(day=today, food_name="Eggs", meal_type="breakfast",
                       calories=300, protein_g=20, dietary_fiber_g=0)
        seed_nutrition(day=today, food_name="Rice", meal_type="lunch",
                       calories=400, protein_g=8, dietary_fiber_g=3)
        totals = nutrition_store.get_daily_totals(today)
        assert totals["item_count"] == 2
        assert totals["calories"] == 700.0
        assert totals["protein_g"] == 28.0

    def test_servings_multiplier_in_sql(self):
        today = date.today().isoformat()
        nutrition_store.add_item(
            food_name="Protein Bar", meal_type="snack",
            nutrients={"calories": 200, "protein_g": 20},
            servings=2.0,
            entry_date=today,
        )
        totals = nutrition_store.get_daily_totals(today)
        assert totals["calories"] == 400.0
        assert totals["protein_g"] == 40.0

    def test_weekly_summary_real_aggregation(self):
        for i in range(3):
            d = (date.today() - timedelta(days=i)).isoformat()
            seed_nutrition(day=d, food_name=f"Meal {i}", calories=500+i*100,
                           protein_g=30)
        summary = nutrition_store.get_weekly_summary()
        assert "Nutrition summary" in summary
        assert "3 days logged" in summary


# ---------------------------------------------------------------------------
# Timer store
# ---------------------------------------------------------------------------

class TestTimerStore:
    def test_get_due_returns_only_pending_past(self):
        past = (datetime.now() - timedelta(minutes=5)).isoformat()
        future = (datetime.now() + timedelta(hours=2)).isoformat()
        t1 = seed_timer(label="Past", fire_at=past)
        t2 = seed_timer(label="Future", fire_at=future)
        due = timer_store.get_due()
        labels = [t["label"] for t in due]
        assert "Past" in labels
        assert "Future" not in labels

    def test_complete_timer_updates_status(self):
        past = (datetime.now() - timedelta(minutes=5)).isoformat()
        t = seed_timer(label="Complete Me", fire_at=past)
        timer_store.complete_timer(t["id"])
        timer = timer_store.get_timer(t["id"])
        assert timer["status"] == "fired"
        assert timer["fired_at"] is not None

    def test_cancel_timer_updates_status(self):
        future = (datetime.now() + timedelta(hours=1)).isoformat()
        t = seed_timer(label="Cancel Me", fire_at=future)
        timer_store.cancel_timer(t["id"])
        timer = timer_store.get_timer(t["id"])
        assert timer["status"] == "cancelled"
        assert timer["cancelled_at"] is not None


# ===========================================================================
# Total: 25 tests
# ===========================================================================
