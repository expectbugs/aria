"""Shared test data factories for the ARIA test suite."""

from datetime import date, time, datetime

from actions import ActionResult


def make_action_result(clean_response="", actions_found=None, action_types=None,
                       failures=None, warnings=None, metadata=None,
                       claims_without_actions=None, expect_actions_missing=None):
    """Create an ActionResult for testing. All list/dict args default to empty."""
    return ActionResult(
        clean_response=clean_response,
        actions_found=actions_found or [],
        action_types=action_types or [],
        failures=failures or [],
        warnings=warnings or [],
        metadata=metadata or {},
        claims_without_actions=claims_without_actions or [],
        expect_actions_missing=expect_actions_missing or [],
    )


def make_event_row(id="abc12345", title="Dentist", d=date(2026, 3, 20),
                   t=time(14, 30), notes=None):
    return {"id": id, "title": title, "date": d, "time": t,
            "notes": notes, "created": datetime(2026, 3, 19, 10, 0, 0)}


def make_reminder_row(id="rem12345", text="Buy milk", due=date(2026, 3, 21),
                      recurring=None, location=None, location_trigger=None,
                      done=False, completed_at=None):
    return {"id": id, "text": text, "due": due, "recurring": recurring,
            "location": location, "location_trigger": location_trigger,
            "done": done, "completed_at": completed_at,
            "created": datetime(2026, 3, 19, 10, 0, 0)}


def make_health_row(id="hlt12345", d=date(2026, 3, 20), category="pain",
                    description="back pain", severity=5, sleep_hours=None,
                    meal_type=None):
    return {"id": id, "date": d, "category": category,
            "description": description, "severity": severity,
            "sleep_hours": sleep_hours, "meal_type": meal_type,
            "created": datetime(2026, 3, 20, 8, 0, 0)}


def make_vehicle_row(id="veh12345", d=date(2026, 3, 15),
                     event_type="oil_change", description="Full synthetic",
                     mileage=145000, cost=45.99):
    return {"id": id, "date": d, "event_type": event_type,
            "description": description, "mileage": mileage, "cost": cost,
            "created": datetime(2026, 3, 15, 10, 0, 0)}


def make_legal_row(id="leg12345", d=date(2026, 3, 18),
                   entry_type="court_date", description="Hearing",
                   contacts=None):
    return {"id": id, "date": d, "entry_type": entry_type,
            "description": description, "contacts": contacts or [],
            "created": datetime(2026, 3, 18)}


def make_timer_row(id="tmr12345", label="Laundry", fire_at=None,
                   delivery="sms", priority="gentle", message="Laundry done",
                   source="user", status="pending"):
    fa = fire_at or datetime(2026, 3, 20, 15, 30)
    return {"id": id, "label": label, "fire_at": fa,
            "delivery": delivery, "priority": priority,
            "message": message, "source": source, "status": status,
            "created": datetime(2026, 3, 20, 15, 0, 0),
            "fired_at": None, "cancelled_at": None}


def make_nutrition_row(id="nut12345", d=date(2026, 3, 20), t=time(12, 30),
                       meal_type="lunch", food_name="Chicken breast",
                       servings=1.0, serving_size="6 oz",
                       nutrients=None, notes="", source="manual"):
    return {"id": id, "date": d, "time": t, "meal_type": meal_type,
            "food_name": food_name, "servings": servings,
            "serving_size": serving_size,
            "nutrients": nutrients or {"calories": 250, "protein_g": 40},
            "notes": notes, "source": source,
            "created": datetime(2026, 3, 20, 12, 30)}


def make_location_row(id=1, lat=42.58, lon=-88.43,
                      location="Rapids Trail, Waukesha, Wisconsin",
                      accuracy_m=10.0, speed_mps=0.0, battery_pct=85):
    return {"id": id,
            "timestamp": datetime(2026, 3, 20, 14, 0, 0),
            "lat": lat, "lon": lon, "location": location,
            "accuracy_m": accuracy_m, "speed_mps": speed_mps,
            "battery_pct": battery_pct}
