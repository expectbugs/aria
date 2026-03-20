"""Integration tests for health_store — real SQL with pattern detection."""

from datetime import datetime, timedelta

import health_store


class TestHealthRoundtrip:
    def test_add_pain_entry(self):
        entry = health_store.add_entry(
            entry_date="2026-03-20", category="pain",
            description="lower back pain", severity=6,
        )
        assert entry["category"] == "pain"
        assert entry["severity"] == 6

    def test_add_meal_entry(self):
        entry = health_store.add_entry(
            entry_date="2026-03-20", category="meal",
            description="grilled salmon", meal_type="dinner",
        )
        assert entry["meal_type"] == "dinner"

    def test_add_sleep_entry(self):
        entry = health_store.add_entry(
            entry_date="2026-03-20", category="sleep",
            description="restful night", sleep_hours=7.5,
        )
        assert entry["sleep_hours"] == 7.5

    def test_filter_by_category(self):
        health_store.add_entry("2026-03-20", "pain", "back")
        health_store.add_entry("2026-03-20", "meal", "lunch")
        health_store.add_entry("2026-03-20", "sleep", "ok", sleep_hours=7)

        pain = health_store.get_entries(category="pain")
        assert len(pain) == 1
        assert pain[0]["category"] == "pain"

    def test_filter_by_days(self):
        today = datetime.now().strftime("%Y-%m-%d")
        old = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        health_store.add_entry(today, "general", "Recent")
        health_store.add_entry(old, "general", "Old")

        recent = health_store.get_entries(days=7)
        assert len(recent) == 1
        assert recent[0]["description"] == "Recent"

    def test_delete(self):
        entry = health_store.add_entry("2026-03-20", "general", "Delete me")
        assert health_store.delete_entry(entry["id"]) is True
        assert health_store.get_entries() == []


class TestPatternsIntegration:
    def test_pain_frequency_detection(self):
        today = datetime.now()
        for i in range(4):
            d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            health_store.add_entry(d, "pain", "lower back pain")

        patterns = health_store.get_patterns(days=7)
        assert any("lower back pain" in p and "4" in p for p in patterns)

    def test_sleep_average(self):
        today = datetime.now()
        for i, hrs in enumerate([6.0, 7.0, 5.0]):
            d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            health_store.add_entry(d, "sleep", "night", sleep_hours=hrs)

        patterns = health_store.get_patterns(days=7)
        assert any("average sleep" in p and "6.0" in p for p in patterns)

    def test_low_sleep_warning(self):
        today = datetime.now()
        for i in range(3):
            d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            health_store.add_entry(d, "sleep", "bad", sleep_hours=4.5)

        patterns = health_store.get_patterns(days=7)
        assert any("below 6" in p for p in patterns)

    def test_fish_tracking(self):
        today = datetime.now().strftime("%Y-%m-%d")
        health_store.add_entry(today, "meal", "grilled salmon", meal_type="dinner")

        patterns = health_store.get_patterns(days=7)
        assert any("fish" in p.lower() for p in patterns)

    def test_no_fish_warning(self):
        today = datetime.now().strftime("%Y-%m-%d")
        health_store.add_entry(today, "meal", "chicken breast", meal_type="dinner")

        patterns = health_store.get_patterns(days=7)
        assert any("no fish" in p.lower() for p in patterns)
