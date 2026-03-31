"""Integration tests for ambient stores — real PostgreSQL on aria_test DB.

Tests the full CRUD lifecycle with real SQL execution, tsvector search,
FK constraints, and conversation grouping.
"""

from datetime import datetime, date, timedelta

import db
import ambient_store
import commitment_store
import person_store


class TestAmbientTranscriptCRUD:
    def test_insert_and_retrieve(self, test_pool):
        result = ambient_store.insert_transcript(
            source="slappy",
            text="I told Mike we would have the proposal ready by Friday",
            started_at="2026-03-20T14:23:01",
            ended_at="2026-03-20T14:23:08",
            duration_s=7.2,
            confidence=0.94,
        )
        assert result["id"] is not None
        assert result["source"] == "slappy"
        assert result["quality_pass"] == "pending"
        assert result["has_wake_word"] is False
        assert result["extracted"] is False

        # Retrieve by ID
        fetched = ambient_store.get_by_id(result["id"])
        assert fetched is not None
        assert fetched["text"] == result["text"]

    def test_get_recent(self, test_pool):
        ambient_store.insert_transcript(
            source="slappy", text="First segment",
            started_at=datetime.now().isoformat(),
        )
        ambient_store.insert_transcript(
            source="slappy", text="Second segment",
            started_at=datetime.now().isoformat(),
        )
        results = ambient_store.get_recent(hours=1)
        assert len(results) == 2

    def test_fulltext_search(self, test_pool):
        ambient_store.insert_transcript(
            source="slappy", text="The budget meeting is on Thursday",
            started_at="2026-03-20T10:00:00",
        )
        ambient_store.insert_transcript(
            source="slappy", text="I need to buy groceries after work",
            started_at="2026-03-20T11:00:00",
        )

        results = ambient_store.search("budget meeting", days=30)
        assert len(results) == 1
        assert "budget" in results[0]["text"]

    def test_quality_pass_lifecycle(self, test_pool):
        t = ambient_store.insert_transcript(
            source="phone", text="Raw transcription",
            started_at="2026-03-20T14:00:00",
            audio_path="/data/ambient/2026-03-20/seg_140000.wav",
        )
        assert t["quality_pass"] == "pending"

        pending = ambient_store.get_pending_quality()
        assert len(pending) == 1

        ambient_store.mark_quality_done(
            t["id"], quality_text="Refined transcription",
            quality_speaker="Mike",
        )

        pending_after = ambient_store.get_pending_quality()
        assert len(pending_after) == 0

        updated = ambient_store.get_by_id(t["id"])
        assert updated["quality_pass"] == "done"
        assert updated["quality_text"] == "Refined transcription"

    def test_extraction_lifecycle(self, test_pool):
        t = ambient_store.insert_transcript(
            source="slappy", text="Some speech",
            started_at="2026-03-20T15:00:00",
        )
        assert t["extracted"] is False

        unextracted = ambient_store.get_unextracted()
        assert len(unextracted) == 1

        count = ambient_store.mark_extracted([t["id"]])
        assert count == 1

        unextracted_after = ambient_store.get_unextracted()
        assert len(unextracted_after) == 0

    def test_audio_cleanup(self, test_pool):
        # Insert an old transcript with audio
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO ambient_transcripts
                   (source, text, started_at, audio_path, created_at)
                   VALUES ('slappy', 'old segment', %s, '/data/old.wav', %s)""",
                (
                    (datetime.now() - timedelta(hours=100)).isoformat(),
                    (datetime.now() - timedelta(hours=100)).isoformat(),
                ),
            )

        count = ambient_store.cleanup_audio(retention_hours=72)
        assert count == 1

    def test_today_stats(self, test_pool):
        ambient_store.insert_transcript(
            source="slappy", text="Hello",
            started_at=datetime.now().isoformat(),
            duration_s=5.0,
        )
        ambient_store.insert_transcript(
            source="slappy", text="World",
            started_at=datetime.now().isoformat(),
            duration_s=3.0,
        )
        assert ambient_store.get_today_count() == 2
        assert ambient_store.get_today_duration() == 8.0


class TestConversationCRUD:
    def test_create_and_assign(self, test_pool):
        t1 = ambient_store.insert_transcript(
            source="slappy", text="First part",
            started_at="2026-03-20T14:00:00",
        )
        t2 = ambient_store.insert_transcript(
            source="slappy", text="Second part",
            started_at="2026-03-20T14:01:00",
        )

        conv = ambient_store.create_conversation(
            started_at="2026-03-20T14:00:00",
            ended_at="2026-03-20T14:02:00",
            duration_s=120.0,
            speakers=["owner", "Mike"],
            location="Banker Wire",
        )
        assert conv["id"] is not None

        count = ambient_store.assign_to_conversation(
            [t1["id"], t2["id"]], conv["id"],
        )
        assert count == 2

        # Verify segment_count updated
        full = ambient_store.get_conversation(conv["id"])
        assert full["segment_count"] == 2
        assert len(full["segments"]) == 2

    def test_update_conversation(self, test_pool):
        conv = ambient_store.create_conversation(
            started_at="2026-03-20T14:00:00",
        )
        result = ambient_store.update_conversation(
            conv["id"], title="Work chat",
            summary="Discussed proposal",
        )
        assert result is True

        updated = ambient_store.get_conversation(conv["id"])
        assert updated["title"] == "Work chat"
        assert updated["summary"] == "Discussed proposal"

    def test_get_conversations(self, test_pool):
        ambient_store.create_conversation(
            started_at=datetime.now().isoformat(),
        )
        results = ambient_store.get_conversations(days=1)
        assert len(results) == 1


class TestDailySummaryCRUD:
    def test_upsert_and_retrieve(self, test_pool):
        result = ambient_store.upsert_daily_summary(
            "2026-03-20", "Productive day",
            key_topics=["proposal", "budget"],
            people_mentioned=["Mike", "Dave"],
            commitments_made=3,
            conversation_count=5,
            total_duration_s=3600.0,
        )
        assert result["summary"] == "Productive day"
        assert result["key_topics"] == ["proposal", "budget"]

        fetched = ambient_store.get_daily_summary("2026-03-20")
        assert fetched["summary"] == "Productive day"

    def test_upsert_replaces(self, test_pool):
        ambient_store.upsert_daily_summary("2026-03-20", "First version")
        ambient_store.upsert_daily_summary("2026-03-20", "Updated version")

        fetched = ambient_store.get_daily_summary("2026-03-20")
        assert fetched["summary"] == "Updated version"


class TestCommitmentCRUD:
    def test_add_and_retrieve(self, test_pool):
        result = commitment_store.add(
            who="self", what="Finish the proposal",
            to_whom="Mike", due_date="2026-03-22",
        )
        assert result["id"] is not None
        assert result["status"] == "open"

        fetched = commitment_store.get_by_id(result["id"])
        assert fetched["what"] == "Finish the proposal"

    def test_complete_lifecycle(self, test_pool):
        c = commitment_store.add(who="self", what="Test task")
        assert commitment_store.complete(c["id"]) is True

        fetched = commitment_store.get_by_id(c["id"])
        assert fetched["status"] == "done"
        assert fetched["completed_at"] is not None

        # Can't complete again
        assert commitment_store.complete(c["id"]) is False

    def test_cancel(self, test_pool):
        c = commitment_store.add(who="self", what="Cancel me")
        assert commitment_store.cancel(c["id"]) is True
        assert commitment_store.get_by_id(c["id"])["status"] == "cancelled"

    def test_get_open(self, test_pool):
        commitment_store.add(who="self", what="Open one")
        c2 = commitment_store.add(who="self", what="Done one")
        commitment_store.complete(c2["id"])

        open_ones = commitment_store.get_open()
        assert len(open_ones) == 1
        assert open_ones[0]["what"] == "Open one"

    def test_get_by_person(self, test_pool):
        commitment_store.add(who="self", what="For Mike", to_whom="Mike")
        commitment_store.add(who="Dave", what="From Dave")

        results = commitment_store.get_by_person("Mike")
        assert len(results) == 1
        assert results[0]["to_whom"] == "Mike"

    def test_expire_overdue(self, test_pool):
        # Insert an old commitment directly
        with db.get_conn() as conn:
            conn.execute(
                """INSERT INTO commitments (who, what, due_date, source, status, created_at)
                   VALUES ('self', 'old task', %s, 'ambient', 'open', NOW())""",
                ((date.today() - timedelta(days=60)).isoformat(),),
            )
        count = commitment_store.expire_overdue(grace_days=30)
        assert count == 1


class TestPersonProfileCRUD:
    def test_upsert_creates(self, test_pool):
        result = person_store.upsert(
            "Mike", relationship="coworker", organization="Banker Wire",
        )
        assert result["name"] == "Mike"
        assert result["relationship"] == "coworker"

    def test_upsert_preserves_existing(self, test_pool):
        person_store.upsert("Mike", relationship="coworker",
                            organization="Banker Wire")
        # Update with None relationship — should preserve
        result = person_store.upsert("Mike", notes="Good guy")
        assert result["relationship"] == "coworker"
        assert result["notes"] == "Good guy"

    def test_search(self, test_pool):
        person_store.upsert("Mike")
        person_store.upsert("Michael", aliases=["Mike Jr"])

        results = person_store.search("Mike")
        assert len(results) >= 1

    def test_record_mention(self, test_pool):
        person_store.upsert("Mike")
        before = person_store.get("Mike")
        assert before["mention_count"] == 0

        assert person_store.record_mention("Mike") is True

        after = person_store.get("Mike")
        assert after["mention_count"] == 1
        assert after["last_mentioned"] is not None

    def test_record_mention_unknown(self, test_pool):
        assert person_store.record_mention("Ghost") is False

    def test_get_names(self, test_pool):
        person_store.upsert("Dave")
        person_store.upsert("Mike")
        names = person_store.get_names()
        assert "Dave" in names
        assert "Mike" in names

    def test_delete(self, test_pool):
        person_store.upsert("Mike")
        assert person_store.delete("Mike") is True
        assert person_store.get("Mike") is None
