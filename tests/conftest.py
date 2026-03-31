"""Shared fixtures for the ARIA test suite.

SAFETY REQUIREMENTS:
  - No real PostgreSQL connections
  - No Claude CLI subprocess spawning
  - No outbound HTTP (Twilio, Fitbit, NWS, Nominatim, phone pushes)
  - No filesystem mutations outside /tmp

Install test deps:
    pip install pytest pytest-asyncio hypothesis
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import date, time, datetime, timedelta, timezone

import pytest

# Ensure project root and tests/ directory are importable
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))


# ---------------------------------------------------------------------------
# Session-level safety guards — prevent real I/O even if individual tests
# forget to mock.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _block_real_database(request):
    """Prevent real PostgreSQL connections in unit tests.

    Integration tests (in tests/integration/) manage their own test database
    and are excluded from this safety guard.
    """
    if "integration" in str(request.fspath):
        yield
        return
    with patch("db.get_pool", return_value=MagicMock()) as m:
        yield m


@pytest.fixture(autouse=True)
def _block_real_subprocess(request):
    """Prevent real Claude CLI subprocess spawning in unit tests.

    Blocks asyncio.create_subprocess_exec to catch any test that forgets
    to mock it. Tests that mock at the module level (e.g.,
    patch("amnesia_pool.asyncio.create_subprocess_exec")) take priority
    over this global guard. Integration tests are excluded.
    """
    if "integration" in str(request.fspath):
        yield
        return
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock,
               side_effect=RuntimeError(
                   "SAFETY: real subprocess spawn blocked in test")) as m:
        yield m


@pytest.fixture(autouse=True, scope="session")
def _block_real_sms():
    """Prevent any real Twilio SMS from being sent."""
    with patch("sms._client", MagicMock()):
        yield


@pytest.fixture(autouse=True)
def _block_real_phone_push(request):
    """Prevent any real image/audio pushes to the phone during tests.

    Blocks push_image.push_image and push_audio.push_audio globally.
    This catches data quality alerts, timer voice delivery, task completion
    delivery, and any other code path that pushes to the phone.

    Excluded for test_push_audio.py and test_push_image.py which test
    those modules directly (they mock httpx.post internally).
    """
    filename = str(request.fspath)
    if "test_push_audio" in filename or "test_push_image" in filename:
        yield
        return
    with patch("push_image.push_image", return_value=True) as m_img, \
         patch("push_audio.push_audio", return_value=True) as m_aud:
        yield m_img, m_aud


@pytest.fixture(autouse=True)
def _disable_sms_redirect():
    """Ensure SMS redirect is off during tests — test actual SMS code paths.

    The redirect tests in test_sms_redirect.py override this with their own
    patch.object(sms.config, "SMS_REDIRECT_TO_IMAGE", True, create=True).
    """
    with patch("sms.config.SMS_REDIRECT_TO_IMAGE", False, create=True):
        yield


@pytest.fixture(autouse=True)
def _block_real_qdrant(request):
    """Prevent real Qdrant connections in unit tests."""
    if "integration" in str(request.fspath):
        yield
        return
    with patch("qdrant_store._client", None):
        yield


@pytest.fixture(autouse=True)
def _block_real_embedding(request):
    """Prevent real sentence-transformers model loading in unit tests."""
    if "integration" in str(request.fspath):
        yield
        return
    with patch("embedding_engine._model", None):
        yield


@pytest.fixture(autouse=True)
def _block_real_neo4j(request):
    """Prevent real Neo4j connections in unit tests."""
    if "integration" in str(request.fspath):
        yield
        return
    with patch("neo4j_store._driver", None):
        yield


# ---------------------------------------------------------------------------
# Reusable helpers
# ---------------------------------------------------------------------------

def make_mock_conn():
    """Create a mock DB connection + context-manager wrapper.

    Returns (mock_conn, patcher) — use as:
        mock_conn, patcher = make_mock_conn()
        with patcher:
            mock_conn.execute.return_value.fetchall.return_value = [...]
            result = some_store.some_func()
    """
    mock_conn = MagicMock()
    return mock_conn


def patch_db(module_path: str):
    """Return (mock_conn, patcher) that patches `<module_path>.db.get_conn`.

    Usage:
        mock_conn, p = patch_db("calendar_store")
        with p:
            mock_conn.execute.return_value.fetchall.return_value = rows
            calendar_store.get_events()
    """
    mock_conn = MagicMock()
    patcher = patch(
        f"{module_path}.db.get_conn",
    )
    mock_get_conn = patcher.start()
    mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn, patcher


# ---------------------------------------------------------------------------
# Sample data factories
# ---------------------------------------------------------------------------

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


def make_transcript_row(id=1, source="slappy", speaker=None,
                        text="I told Mike we'd have the proposal ready by Friday",
                        started_at=None, ended_at=None, duration_s=7.2,
                        confidence=0.94, quality_pass="pending",
                        quality_text=None, quality_speaker=None,
                        audio_path=None, has_wake_word=False,
                        extracted=False, conversation_id=None):
    sa = started_at or datetime(2026, 3, 20, 14, 23, 1)
    ea = ended_at or datetime(2026, 3, 20, 14, 23, 8)
    return {"id": id, "source": source, "speaker": speaker,
            "text": text, "started_at": sa, "ended_at": ea,
            "duration_s": duration_s, "confidence": confidence,
            "quality_pass": quality_pass, "quality_text": quality_text,
            "quality_speaker": quality_speaker,
            "audio_path": audio_path, "has_wake_word": has_wake_word,
            "extracted": extracted, "conversation_id": conversation_id,
            "created_at": datetime(2026, 3, 20, 14, 23, 1)}


def make_commitment_row(id=1, who="self", what="Have proposal ready by Friday",
                        to_whom="Mike", due_date=date(2026, 3, 22),
                        source="ambient", source_id=None,
                        conversation_id=None, status="open",
                        completed_at=None):
    return {"id": id, "who": who, "what": what, "to_whom": to_whom,
            "due_date": due_date, "source": source, "source_id": source_id,
            "conversation_id": conversation_id, "status": status,
            "completed_at": completed_at,
            "created_at": datetime(2026, 3, 20, 14, 23, 0)}


def make_person_row(id=1, name="Mike", aliases=None,
                    relationship="coworker", organization="Banker Wire",
                    notes=None, mention_count=12, last_mentioned=None):
    lm = last_mentioned or datetime(2026, 3, 20, 14, 23, 0)
    return {"id": id, "name": name, "aliases": aliases or [],
            "relationship": relationship, "organization": organization,
            "notes": notes, "mention_count": mention_count,
            "last_mentioned": lm,
            "created_at": datetime(2026, 3, 15, 8, 0, 0)}
