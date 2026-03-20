"""Integration tests for vehicle_store — real SQL against PostgreSQL."""

import vehicle_store


class TestVehicleRoundtrip:
    def test_add_and_retrieve(self):
        entry = vehicle_store.add_entry(
            event_date="2026-03-15", event_type="oil_change",
            description="Full synthetic 5W-30", mileage=145000, cost=45.99,
        )
        assert entry["event_type"] == "oil_change"
        assert entry["mileage"] == 145000
        assert entry["cost"] == 45.99

        entries = vehicle_store.get_entries()
        assert len(entries) == 1

    def test_null_optional_fields(self):
        entry = vehicle_store.add_entry(
            event_date="2026-03-15", event_type="inspection",
            description="Annual inspection",
        )
        assert entry["mileage"] is None
        assert entry["cost"] is None

    def test_limit(self):
        for i in range(5):
            vehicle_store.add_entry(
                event_date=f"2026-03-{10+i:02d}", event_type="general",
                description=f"Entry {i}",
            )
        assert len(vehicle_store.get_entries(limit=3)) == 3

    def test_filter_by_event_type(self):
        vehicle_store.add_entry("2026-03-10", "oil_change", "Oil")
        vehicle_store.add_entry("2026-03-11", "tire_rotation", "Tires")
        vehicle_store.add_entry("2026-03-12", "oil_change", "Oil again")

        oil = vehicle_store.get_entries(event_type="oil_change")
        assert len(oil) == 2
        assert all(e["event_type"] == "oil_change" for e in oil)

    def test_ordered_by_date_desc(self):
        vehicle_store.add_entry("2026-03-10", "general", "Older")
        vehicle_store.add_entry("2026-03-15", "general", "Newer")

        entries = vehicle_store.get_entries()
        assert entries[0]["description"] == "Newer"

    def test_get_latest_by_type(self):
        vehicle_store.add_entry("2026-01-01", "oil_change", "Old oil")
        vehicle_store.add_entry("2026-03-15", "oil_change", "New oil")
        vehicle_store.add_entry("2026-02-01", "tire_rotation", "Tires")

        latest = vehicle_store.get_latest_by_type()
        assert latest["oil_change"]["description"] == "New oil"
        assert "tire_rotation" in latest

    def test_delete(self):
        entry = vehicle_store.add_entry("2026-03-15", "general", "Delete me")
        assert vehicle_store.delete_entry(entry["id"]) is True
        assert vehicle_store.get_entries() == []
