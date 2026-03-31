"""Tests for ambient_store.py — transcript and conversation CRUD."""

from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import ambient_store
from conftest import patch_db, make_transcript_row


def _patch():
    return patch_db("ambient_store")


# === Transcripts ===

class TestInsertTranscript:
    def test_insert_returns_serialized(self):
        mc, p = _patch()
        mc.execute.return_value.fetchone.return_value = make_transcript_row()
        try:
            result = ambient_store.insert_transcript(
                source="slappy",
                text="I told Mike we'd have the proposal ready by Friday",
                started_at="2026-03-20T14:23:01",
                ended_at="2026-03-20T14:23:08",
                duration_s=7.2,
                confidence=0.94,
            )
            assert result["id"] == 1
            assert result["source"] == "slappy"
            assert result["text"] == "I told Mike we'd have the proposal ready by Friday"
            assert result["confidence"] == 0.94
        finally:
            p.stop()

    def test_insert_passes_all_fields(self):
        mc, p = _patch()
        mc.execute.return_value.fetchone.return_value = make_transcript_row(
            has_wake_word=True, audio_path="/data/ambient/seg.wav",
        )
        try:
            ambient_store.insert_transcript(
                source="phone", text="ARIA set a timer",
                started_at="2026-03-20T14:00:00",
                audio_path="/data/ambient/seg.wav",
                has_wake_word=True,
            )
            sql = mc.execute.call_args[0][0]
            assert "INSERT INTO ambient_transcripts" in sql
            params = mc.execute.call_args[0][1]
            assert params[0] == "phone"
            assert params[7] is None  # speaker
            assert params[8] is True  # has_wake_word
        finally:
            p.stop()


class TestGetRecent:
    def test_returns_list(self):
        mc, p = _patch()
        mc.execute.return_value.fetchall.return_value = [
            make_transcript_row(id=1),
            make_transcript_row(id=2, text="Another segment"),
        ]
        try:
            results = ambient_store.get_recent(hours=2)
            assert len(results) == 2
            assert results[0]["id"] == 1
        finally:
            p.stop()

    def test_passes_cutoff_and_limit(self):
        mc, p = _patch()
        mc.execute.return_value.fetchall.return_value = []
        try:
            ambient_store.get_recent(hours=6, limit=10)
            sql = mc.execute.call_args[0][0]
            params = mc.execute.call_args[0][1]
            assert "started_at >= %s" in sql
            assert "LIMIT %s" in sql
            assert params[1] == 10
        finally:
            p.stop()

    def test_empty_result(self):
        mc, p = _patch()
        mc.execute.return_value.fetchall.return_value = []
        try:
            assert ambient_store.get_recent() == []
        finally:
            p.stop()


class TestSearch:
    def test_uses_websearch_tsquery(self):
        mc, p = _patch()
        mc.execute.return_value.fetchall.return_value = [
            make_transcript_row(text="proposal for Friday"),
        ]
        try:
            results = ambient_store.search("Friday proposal", days=7)
            sql = mc.execute.call_args[0][0]
            assert "websearch_to_tsquery" in sql
            assert "ts_rank" in sql
            assert len(results) == 1
        finally:
            p.stop()

    def test_passes_days_cutoff(self):
        mc, p = _patch()
        mc.execute.return_value.fetchall.return_value = []
        try:
            ambient_store.search("test", days=14, limit=5)
            params = mc.execute.call_args[0][1]
            assert params[3] == 5  # limit
        finally:
            p.stop()


class TestGetById:
    def test_found(self):
        mc, p = _patch()
        mc.execute.return_value.fetchone.return_value = make_transcript_row(id=42)
        try:
            result = ambient_store.get_by_id(42)
            assert result["id"] == 42
        finally:
            p.stop()

    def test_not_found(self):
        mc, p = _patch()
        mc.execute.return_value.fetchone.return_value = None
        try:
            assert ambient_store.get_by_id(999) is None
        finally:
            p.stop()


class TestQualityPass:
    def test_get_pending(self):
        mc, p = _patch()
        mc.execute.return_value.fetchall.return_value = [
            make_transcript_row(quality_pass="pending", audio_path="/data/ambient/seg.wav"),
        ]
        try:
            results = ambient_store.get_pending_quality()
            sql = mc.execute.call_args[0][0]
            assert "quality_pass = 'pending'" in sql
            assert "audio_path IS NOT NULL" in sql
            assert len(results) == 1
        finally:
            p.stop()

    def test_mark_done(self):
        mc, p = _patch()
        mc.execute.return_value.rowcount = 1
        try:
            assert ambient_store.mark_quality_done(42, "refined text", "Mike") is True
            params = mc.execute.call_args[0][1]
            assert params[0] == "refined text"
            assert params[1] == "Mike"
            assert params[2] == 42
        finally:
            p.stop()


class TestExtraction:
    def test_get_unextracted(self):
        mc, p = _patch()
        mc.execute.return_value.fetchall.return_value = [
            make_transcript_row(extracted=False),
        ]
        try:
            results = ambient_store.get_unextracted()
            sql = mc.execute.call_args[0][0]
            assert "extracted = FALSE" in sql
            assert len(results) == 1
        finally:
            p.stop()

    def test_mark_extracted(self):
        mc, p = _patch()
        mc.execute.return_value.rowcount = 3
        try:
            count = ambient_store.mark_extracted([1, 2, 3])
            assert count == 3
            sql = mc.execute.call_args[0][0]
            assert "ANY(%s)" in sql
        finally:
            p.stop()

    def test_mark_extracted_empty_list(self):
        mc, p = _patch()
        try:
            assert ambient_store.mark_extracted([]) == 0
        finally:
            p.stop()


class TestCleanupAudio:
    def test_clears_audio_path(self):
        mc, p = _patch()
        mc.execute.return_value.rowcount = 5
        try:
            count = ambient_store.cleanup_audio(retention_hours=72)
            assert count == 5
            sql = mc.execute.call_args[0][0]
            assert "SET audio_path = NULL" in sql
            assert "audio_path IS NOT NULL" in sql
        finally:
            p.stop()


class TestTodayStats:
    def test_get_today_count(self):
        mc, p = _patch()
        mc.execute.return_value.fetchone.return_value = {"cnt": 47}
        try:
            assert ambient_store.get_today_count() == 47
        finally:
            p.stop()

    def test_get_today_duration(self):
        mc, p = _patch()
        mc.execute.return_value.fetchone.return_value = {"total": 1234.5}
        try:
            assert ambient_store.get_today_duration() == 1234.5
        finally:
            p.stop()


# === Conversations ===

class TestConversations:
    def test_create(self):
        mc, p = _patch()
        mc.execute.return_value.fetchone.return_value = {
            "id": 1, "title": None, "summary": None,
            "started_at": datetime(2026, 3, 20, 14, 0),
            "ended_at": datetime(2026, 3, 20, 14, 5),
            "duration_s": 300.0, "segment_count": 0,
            "speakers": ["owner", "Mike"], "location": "Banker Wire",
            "created_at": datetime(2026, 3, 20, 14, 0),
        }
        try:
            result = ambient_store.create_conversation(
                started_at="2026-03-20T14:00:00",
                ended_at="2026-03-20T14:05:00",
                duration_s=300.0,
                speakers=["owner", "Mike"],
                location="Banker Wire",
            )
            assert result["id"] == 1
            assert result["speakers"] == ["owner", "Mike"]
        finally:
            p.stop()

    def test_get_conversations(self):
        mc, p = _patch()
        mc.execute.return_value.fetchall.return_value = []
        try:
            results = ambient_store.get_conversations(days=3)
            sql = mc.execute.call_args[0][0]
            assert "started_at >= %s" in sql
            assert results == []
        finally:
            p.stop()

    def test_assign_to_conversation(self):
        mc, p = _patch()
        mc.execute.return_value.rowcount = 3
        try:
            count = ambient_store.assign_to_conversation([1, 2, 3], 10)
            assert count == 3
            # Should have been called twice (UPDATE transcripts + UPDATE conversation count)
            assert mc.execute.call_count == 2
        finally:
            p.stop()

    def test_assign_empty_list(self):
        mc, p = _patch()
        try:
            assert ambient_store.assign_to_conversation([], 10) == 0
        finally:
            p.stop()

    def test_update_conversation(self):
        mc, p = _patch()
        mc.execute.return_value.rowcount = 1
        try:
            result = ambient_store.update_conversation(
                1, title="Work chat", summary="Discussed proposal",
            )
            assert result is True
            sql = mc.execute.call_args[0][0]
            assert "title = %s" in sql
            assert "summary = %s" in sql
        finally:
            p.stop()

    def test_update_no_fields(self):
        mc, p = _patch()
        try:
            assert ambient_store.update_conversation(1) is False
        finally:
            p.stop()


# === Daily summaries ===

class TestDailySummaries:
    def test_upsert(self):
        mc, p = _patch()
        mc.execute.return_value.fetchone.return_value = {
            "date": "2026-03-20", "summary": "Busy day",
            "key_topics": ["proposal"], "people_mentioned": ["Mike"],
            "commitments_made": 2, "conversation_count": 5,
            "total_duration_s": 1800.0,
            "created_at": datetime(2026, 3, 20, 23, 50),
        }
        try:
            result = ambient_store.upsert_daily_summary(
                "2026-03-20", "Busy day",
                key_topics=["proposal"], people_mentioned=["Mike"],
                commitments_made=2, conversation_count=5,
                total_duration_s=1800.0,
            )
            sql = mc.execute.call_args[0][0]
            assert "ON CONFLICT (date) DO UPDATE" in sql
            assert result["summary"] == "Busy day"
        finally:
            p.stop()

    def test_get_daily_summary_found(self):
        mc, p = _patch()
        mc.execute.return_value.fetchone.return_value = {
            "date": "2026-03-20", "summary": "Busy day",
            "key_topics": [], "people_mentioned": [],
            "commitments_made": 0, "conversation_count": 0,
            "total_duration_s": 0,
            "created_at": datetime(2026, 3, 20, 23, 50),
        }
        try:
            result = ambient_store.get_daily_summary("2026-03-20")
            assert result["summary"] == "Busy day"
        finally:
            p.stop()

    def test_get_daily_summary_not_found(self):
        mc, p = _patch()
        mc.execute.return_value.fetchone.return_value = None
        try:
            assert ambient_store.get_daily_summary("2026-01-01") is None
        finally:
            p.stop()
