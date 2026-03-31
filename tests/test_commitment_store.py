"""Tests for commitment_store.py — promise tracking CRUD."""

from datetime import date, datetime
from unittest.mock import patch, MagicMock

import commitment_store
from conftest import patch_db, make_commitment_row


def _patch():
    return patch_db("commitment_store")


class TestAdd:
    def test_basic_add(self):
        mc, p = _patch()
        mc.execute.return_value.fetchone.return_value = make_commitment_row()
        try:
            result = commitment_store.add(
                who="self", what="Have proposal ready by Friday",
                to_whom="Mike", due_date="2026-03-22",
            )
            assert result["id"] == 1
            assert result["who"] == "self"
            assert result["what"] == "Have proposal ready by Friday"
            assert result["status"] == "open"
        finally:
            p.stop()

    def test_add_with_source(self):
        mc, p = _patch()
        mc.execute.return_value.fetchone.return_value = make_commitment_row(
            source="email", source_id=42,
        )
        try:
            result = commitment_store.add(
                who="Dave", what="Send invoice",
                source="email", source_id=42,
            )
            sql = mc.execute.call_args[0][0]
            assert "INSERT INTO commitments" in sql
            params = mc.execute.call_args[0][1]
            assert params[4] == "email"  # source
            assert params[5] == 42       # source_id
        finally:
            p.stop()


class TestGetOpen:
    def test_returns_open_commitments(self):
        mc, p = _patch()
        mc.execute.return_value.fetchall.return_value = [
            make_commitment_row(id=1, what="Proposal"),
            make_commitment_row(id=2, what="Invoice"),
        ]
        try:
            results = commitment_store.get_open()
            assert len(results) == 2
            sql = mc.execute.call_args[0][0]
            assert "status = 'open'" in sql
            assert "due_date ASC NULLS LAST" in sql
        finally:
            p.stop()

    def test_empty(self):
        mc, p = _patch()
        mc.execute.return_value.fetchall.return_value = []
        try:
            assert commitment_store.get_open() == []
        finally:
            p.stop()


class TestGetOverdue:
    def test_returns_past_due(self):
        mc, p = _patch()
        mc.execute.return_value.fetchall.return_value = [
            make_commitment_row(due_date=date(2026, 3, 15)),
        ]
        try:
            results = commitment_store.get_overdue()
            sql = mc.execute.call_args[0][0]
            assert "status = 'open'" in sql
            assert "due_date IS NOT NULL" in sql
            assert "due_date < %s" in sql
            assert len(results) == 1
        finally:
            p.stop()


class TestGetDueToday:
    def test_returns_todays_commitments(self):
        mc, p = _patch()
        mc.execute.return_value.fetchall.return_value = []
        try:
            commitment_store.get_due_today()
            sql = mc.execute.call_args[0][0]
            assert "due_date = %s" in sql
        finally:
            p.stop()


class TestGetByPerson:
    def test_searches_who_and_to_whom(self):
        mc, p = _patch()
        mc.execute.return_value.fetchall.return_value = [
            make_commitment_row(who="self", to_whom="Mike"),
        ]
        try:
            results = commitment_store.get_by_person("Mike")
            sql = mc.execute.call_args[0][0]
            assert "who ILIKE %s" in sql
            assert "to_whom ILIKE %s" in sql
            assert len(results) == 1
        finally:
            p.stop()

    def test_with_status_filter(self):
        mc, p = _patch()
        mc.execute.return_value.fetchall.return_value = []
        try:
            commitment_store.get_by_person("Mike", status="done")
            sql = mc.execute.call_args[0][0]
            assert "status = %s" in sql
        finally:
            p.stop()


class TestGetRecent:
    def test_returns_recent(self):
        mc, p = _patch()
        mc.execute.return_value.fetchall.return_value = []
        try:
            commitment_store.get_recent(days=3, limit=10)
            sql = mc.execute.call_args[0][0]
            params = mc.execute.call_args[0][1]
            assert "created_at >= %s" in sql
            assert params[1] == 10
        finally:
            p.stop()


class TestComplete:
    def test_marks_done(self):
        mc, p = _patch()
        mc.execute.return_value.rowcount = 1
        try:
            assert commitment_store.complete(42) is True
            sql = mc.execute.call_args[0][0]
            assert "status = 'done'" in sql
            assert "completed_at = NOW()" in sql
            assert "status = 'open'" in sql  # only open can be completed
        finally:
            p.stop()

    def test_not_found(self):
        mc, p = _patch()
        mc.execute.return_value.rowcount = 0
        try:
            assert commitment_store.complete(999) is False
        finally:
            p.stop()


class TestCancel:
    def test_marks_cancelled(self):
        mc, p = _patch()
        mc.execute.return_value.rowcount = 1
        try:
            assert commitment_store.cancel(42) is True
            sql = mc.execute.call_args[0][0]
            assert "status = 'cancelled'" in sql
        finally:
            p.stop()


class TestExpireOverdue:
    def test_expires_old_commitments(self):
        mc, p = _patch()
        mc.execute.return_value.rowcount = 3
        try:
            count = commitment_store.expire_overdue(grace_days=30)
            assert count == 3
            sql = mc.execute.call_args[0][0]
            assert "status = 'expired'" in sql
            assert "due_date < %s" in sql
        finally:
            p.stop()

    def test_nothing_to_expire(self):
        mc, p = _patch()
        mc.execute.return_value.rowcount = 0
        try:
            assert commitment_store.expire_overdue() == 0
        finally:
            p.stop()


class TestGetById:
    def test_found(self):
        mc, p = _patch()
        mc.execute.return_value.fetchone.return_value = make_commitment_row(id=42)
        try:
            result = commitment_store.get_by_id(42)
            assert result["id"] == 42
        finally:
            p.stop()

    def test_not_found(self):
        mc, p = _patch()
        mc.execute.return_value.fetchone.return_value = None
        try:
            assert commitment_store.get_by_id(999) is None
        finally:
            p.stop()
