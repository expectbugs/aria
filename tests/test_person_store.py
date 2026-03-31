"""Tests for person_store.py — person profile CRUD."""

from datetime import datetime
from unittest.mock import patch, MagicMock

import person_store
from conftest import patch_db, make_person_row


def _patch():
    return patch_db("person_store")


class TestUpsert:
    def test_creates_new_person(self):
        mc, p = _patch()
        mc.execute.return_value.fetchone.return_value = make_person_row()
        try:
            result = person_store.upsert(
                "Mike", relationship="coworker", organization="Banker Wire",
            )
            assert result["name"] == "Mike"
            assert result["relationship"] == "coworker"
            sql = mc.execute.call_args[0][0]
            assert "ON CONFLICT (name) DO UPDATE" in sql
        finally:
            p.stop()

    def test_preserves_existing_with_coalesce(self):
        mc, p = _patch()
        mc.execute.return_value.fetchone.return_value = make_person_row()
        try:
            # When relationship=None, existing relationship should be preserved
            person_store.upsert("Mike")
            sql = mc.execute.call_args[0][0]
            assert "COALESCE(EXCLUDED.relationship, person_profiles.relationship)" in sql
            assert "COALESCE(EXCLUDED.organization, person_profiles.organization)" in sql
        finally:
            p.stop()

    def test_with_aliases(self):
        mc, p = _patch()
        mc.execute.return_value.fetchone.return_value = make_person_row(
            aliases=["Michael", "Mikey"],
        )
        try:
            result = person_store.upsert("Mike", aliases=["Michael", "Mikey"])
            params = mc.execute.call_args[0][1]
            assert params[4] == ["Michael", "Mikey"]  # aliases
        finally:
            p.stop()


class TestGet:
    def test_found(self):
        mc, p = _patch()
        mc.execute.return_value.fetchone.return_value = make_person_row()
        try:
            result = person_store.get("Mike")
            assert result["name"] == "Mike"
            assert result["mention_count"] == 12
        finally:
            p.stop()

    def test_not_found(self):
        mc, p = _patch()
        mc.execute.return_value.fetchone.return_value = None
        try:
            assert person_store.get("Unknown") is None
        finally:
            p.stop()


class TestGetById:
    def test_found(self):
        mc, p = _patch()
        mc.execute.return_value.fetchone.return_value = make_person_row(id=5)
        try:
            result = person_store.get_by_id(5)
            assert result["id"] == 5
        finally:
            p.stop()


class TestSearch:
    def test_searches_name_and_aliases(self):
        mc, p = _patch()
        mc.execute.return_value.fetchall.return_value = [make_person_row()]
        try:
            results = person_store.search("Mike")
            sql = mc.execute.call_args[0][0]
            assert "name ILIKE %s" in sql
            assert "unnest(aliases)" in sql
            assert len(results) == 1
        finally:
            p.stop()

    def test_empty_result(self):
        mc, p = _patch()
        mc.execute.return_value.fetchall.return_value = []
        try:
            assert person_store.search("Nobody") == []
        finally:
            p.stop()


class TestGetAll:
    def test_ordered_by_mention_count(self):
        mc, p = _patch()
        mc.execute.return_value.fetchall.return_value = [
            make_person_row(id=1, name="Mike", mention_count=12),
            make_person_row(id=2, name="Dave", mention_count=5),
        ]
        try:
            results = person_store.get_all()
            sql = mc.execute.call_args[0][0]
            assert "ORDER BY mention_count DESC" in sql
            assert len(results) == 2
        finally:
            p.stop()


class TestRecordMention:
    def test_increments_count(self):
        mc, p = _patch()
        mc.execute.return_value.rowcount = 1
        try:
            assert person_store.record_mention("Mike") is True
            sql = mc.execute.call_args[0][0]
            assert "mention_count = mention_count + 1" in sql
            assert "last_mentioned = NOW()" in sql
        finally:
            p.stop()

    def test_person_not_found(self):
        mc, p = _patch()
        mc.execute.return_value.rowcount = 0
        try:
            assert person_store.record_mention("Unknown") is False
        finally:
            p.stop()


class TestGetNames:
    def test_returns_name_list(self):
        mc, p = _patch()
        mc.execute.return_value.fetchall.return_value = [
            {"name": "Dave"}, {"name": "Mike"},
        ]
        try:
            names = person_store.get_names()
            assert names == ["Dave", "Mike"]
        finally:
            p.stop()

    def test_empty(self):
        mc, p = _patch()
        mc.execute.return_value.fetchall.return_value = []
        try:
            assert person_store.get_names() == []
        finally:
            p.stop()


class TestDelete:
    def test_deletes(self):
        mc, p = _patch()
        mc.execute.return_value.rowcount = 1
        try:
            assert person_store.delete("Mike") is True
        finally:
            p.stop()

    def test_not_found(self):
        mc, p = _patch()
        mc.execute.return_value.rowcount = 0
        try:
            assert person_store.delete("Nobody") is False
        finally:
            p.stop()
