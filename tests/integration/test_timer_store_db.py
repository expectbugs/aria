"""Integration tests for timer_store — real SQL against PostgreSQL."""

from datetime import datetime, timedelta

import timer_store


class TestTimerRoundtrip:
    def test_add_and_retrieve(self):
        timer = timer_store.add_timer(
            label="Laundry",
            fire_at=(datetime.now() + timedelta(minutes=30)).isoformat(),
            delivery="sms", message="Laundry done!",
        )
        assert timer["label"] == "Laundry"
        assert timer["status"] == "pending"
        assert timer["delivery"] == "sms"

    def test_get_active(self):
        timer_store.add_timer("A", (datetime.now() + timedelta(hours=2)).isoformat())
        timer_store.add_timer("B", (datetime.now() + timedelta(hours=1)).isoformat())

        active = timer_store.get_active()
        assert len(active) == 2
        # Should be ordered by fire_at
        assert active[0]["label"] == "B"

    def test_get_due(self):
        # Past timer — should be due
        past = (datetime.now() - timedelta(minutes=5)).isoformat()
        timer_store.add_timer("Past", past)

        # Future timer — should not be due
        future = (datetime.now() + timedelta(hours=1)).isoformat()
        timer_store.add_timer("Future", future)

        due = timer_store.get_due()
        assert len(due) == 1
        assert due[0]["label"] == "Past"

    def test_cancel_timer(self):
        timer = timer_store.add_timer("Cancel me",
                                       (datetime.now() + timedelta(hours=1)).isoformat())
        assert timer_store.cancel_timer(timer["id"]) is True

        # Should no longer appear in active
        assert timer_store.get_active() == []

        # Verify status changed
        t = timer_store.get_timer(timer["id"])
        assert t["status"] == "cancelled"

    def test_cancel_already_fired_fails(self):
        timer = timer_store.add_timer("Fire me",
                                       (datetime.now() - timedelta(minutes=1)).isoformat())
        timer_store.complete_timer(timer["id"])
        # Cancel should fail — already fired
        assert timer_store.cancel_timer(timer["id"]) is False

    def test_complete_timer(self):
        timer = timer_store.add_timer("Complete",
                                       (datetime.now() - timedelta(minutes=1)).isoformat())
        assert timer_store.complete_timer(timer["id"]) is True

        t = timer_store.get_timer(timer["id"])
        assert t["status"] == "fired"

    def test_get_timer_by_id(self):
        timer = timer_store.add_timer("Find me",
                                       (datetime.now() + timedelta(hours=1)).isoformat())
        found = timer_store.get_timer(timer["id"])
        assert found["label"] == "Find me"

    def test_get_timer_not_found(self):
        assert timer_store.get_timer("nonexist") is None

    def test_voice_delivery_and_priority(self):
        timer = timer_store.add_timer(
            "Alarm", (datetime.now() + timedelta(hours=1)).isoformat(),
            delivery="voice", priority="urgent", message="Wake up!",
        )
        assert timer["delivery"] == "voice"
        assert timer["priority"] == "urgent"

    def test_system_source(self):
        timer = timer_store.add_timer(
            "Nudge", (datetime.now() + timedelta(hours=1)).isoformat(),
            source="system",
        )
        assert timer["source"] == "system"

    def test_timestamptz_comparison(self):
        """Verify fire_at TIMESTAMPTZ comparison works with naive datetime."""
        fire = (datetime.now() + timedelta(seconds=1)).isoformat()
        timer_store.add_timer("Soon", fire)

        # Should not be due yet
        assert timer_store.get_due() == []

        # Should be due with a future 'now'
        future = datetime.now() + timedelta(minutes=5)
        due = timer_store.get_due(now=future)
        assert len(due) == 1
