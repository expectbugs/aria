"""Tests for monitor.py — ARIA system health monitor.

Tests cover:
- Quiet hours suppression for non-critical alerts
- Critical alerts (daemon/postgres) bypass quiet hours
- Cooldown NOT updated when push_alert delivery fails
- Cooldown updated when push_alert delivery succeeds
- State cleanup runs on every execution

SAFETY: All DB, push_image, and SMS calls are mocked. No real I/O.
"""

import time
from unittest.mock import patch, MagicMock, call

import monitor


class TestQuietHours:
    """Tests for quiet hours suppression logic."""

    def test_is_quiet_hours_within_range(self):
        """Hours 0-6 are quiet hours (default 0-7)."""
        with patch.object(monitor.config, "QUIET_HOURS_START", 0, create=True), \
             patch.object(monitor.config, "QUIET_HOURS_END", 7, create=True):
            # 3 AM is quiet
            with patch("monitor.datetime") as mock_dt:
                mock_dt.now.return_value.hour = 3
                mock_dt.now.return_value.strftime = lambda fmt: "2026-03-27 03:00:00"
                assert monitor.is_quiet_hours() is True

    def test_is_quiet_hours_outside_range(self):
        """Hour 10 is not quiet hours."""
        with patch.object(monitor.config, "QUIET_HOURS_START", 0, create=True), \
             patch.object(monitor.config, "QUIET_HOURS_END", 7, create=True):
            with patch("monitor.datetime") as mock_dt:
                mock_dt.now.return_value.hour = 10
                assert monitor.is_quiet_hours() is False

    def test_is_quiet_hours_boundary_start(self):
        """Hour 0 (midnight) IS quiet hours."""
        with patch.object(monitor.config, "QUIET_HOURS_START", 0, create=True), \
             patch.object(monitor.config, "QUIET_HOURS_END", 7, create=True):
            with patch("monitor.datetime") as mock_dt:
                mock_dt.now.return_value.hour = 0
                assert monitor.is_quiet_hours() is True

    def test_is_quiet_hours_boundary_end(self):
        """Hour 7 is NOT quiet hours (end is exclusive)."""
        with patch.object(monitor.config, "QUIET_HOURS_START", 0, create=True), \
             patch.object(monitor.config, "QUIET_HOURS_END", 7, create=True):
            with patch("monitor.datetime") as mock_dt:
                mock_dt.now.return_value.hour = 7
                assert monitor.is_quiet_hours() is False

    def test_quiet_hours_suppression(self):
        """Non-critical alerts (redis, backup, peer) suppressed during quiet hours."""
        with patch("monitor.check_daemon", return_value=None), \
             patch("monitor.check_postgres", return_value=None), \
             patch("monitor.check_redis", return_value="Redis unavailable"), \
             patch("monitor.check_backup_freshness", return_value=None), \
             patch("monitor.check_restore_freshness", return_value=None), \
             patch("monitor.check_peer", return_value=None), \
             patch("monitor.is_quiet_hours", return_value=True), \
             patch("monitor.load_state", return_value={}), \
             patch("monitor.save_state"), \
             patch("monitor.push_alert") as mock_push:

            monitor.main()

            mock_push.assert_not_called()

    def test_critical_alerts_bypass_quiet_hours(self):
        """Daemon and postgres failures still alert during quiet hours."""
        with patch("monitor.check_daemon", return_value="Daemon unreachable"), \
             patch("monitor.check_postgres", return_value=None), \
             patch("monitor.check_redis", return_value=None), \
             patch("monitor.check_backup_freshness", return_value=None), \
             patch("monitor.check_restore_freshness", return_value=None), \
             patch("monitor.check_peer", return_value=None), \
             patch("monitor.is_quiet_hours", return_value=True), \
             patch("monitor.load_state", return_value={}), \
             patch("monitor.save_state"), \
             patch("monitor.push_alert", return_value=True) as mock_push:

            monitor.main()

            mock_push.assert_called_once()
            failures = mock_push.call_args[0][1]
            assert "Daemon unreachable" in failures

    def test_postgres_failure_bypasses_quiet_hours(self):
        """Postgres failures are also critical — bypass quiet hours."""
        with patch("monitor.check_daemon", return_value=None), \
             patch("monitor.check_postgres", return_value="PostgreSQL error: connection refused"), \
             patch("monitor.check_redis", return_value=None), \
             patch("monitor.check_backup_freshness", return_value=None), \
             patch("monitor.check_restore_freshness", return_value=None), \
             patch("monitor.check_peer", return_value=None), \
             patch("monitor.is_quiet_hours", return_value=True), \
             patch("monitor.load_state", return_value={}), \
             patch("monitor.save_state"), \
             patch("monitor.push_alert", return_value=True) as mock_push:

            monitor.main()

            mock_push.assert_called_once()


class TestCooldownOnDelivery:
    """Tests for cooldown gated on delivery success."""

    def test_cooldown_not_updated_on_delivery_failure(self):
        """push_alert returns False -> cooldown NOT updated."""
        with patch("monitor.check_daemon", return_value="Daemon unreachable"), \
             patch("monitor.check_postgres", return_value=None), \
             patch("monitor.check_redis", return_value=None), \
             patch("monitor.check_backup_freshness", return_value=None), \
             patch("monitor.check_restore_freshness", return_value=None), \
             patch("monitor.check_peer", return_value=None), \
             patch("monitor.is_quiet_hours", return_value=False), \
             patch("monitor.load_state", return_value={}) as mock_load, \
             patch("monitor.save_state") as mock_save, \
             patch("monitor.push_alert", return_value=False):

            monitor.main()

            # save_state called once for cleanup at the top, but NOT again
            # for cooldown update (since delivery failed)
            assert mock_save.call_count == 1
            # The single save_state call should have empty dict (cleanup of empty state)
            saved_state = mock_save.call_args[0][0]
            # Should not contain any failure key timestamp
            assert len(saved_state) == 0

    def test_cooldown_updated_on_success(self):
        """push_alert returns True -> cooldown IS updated."""
        with patch("monitor.check_daemon", return_value="Daemon unreachable"), \
             patch("monitor.check_postgres", return_value=None), \
             patch("monitor.check_redis", return_value=None), \
             patch("monitor.check_backup_freshness", return_value=None), \
             patch("monitor.check_restore_freshness", return_value=None), \
             patch("monitor.check_peer", return_value=None), \
             patch("monitor.is_quiet_hours", return_value=False), \
             patch("monitor.load_state", return_value={}) as mock_load, \
             patch("monitor.save_state") as mock_save, \
             patch("monitor.push_alert", return_value=True), \
             patch("monitor.time") as mock_time:

            mock_time.time.return_value = 1000000.0

            monitor.main()

            # save_state called twice: once for cleanup, once for cooldown update
            assert mock_save.call_count == 2
            # Second call should have the failure key with the timestamp
            saved_state = mock_save.call_args_list[1][0][0]
            assert any(v == 1000000.0 for v in saved_state.values())


class TestPushAlertReturnValue:
    """Tests for push_alert() returning bool."""

    @patch("monitor.generate_svg", return_value="<svg/>")
    def test_returns_true_on_image_push_success(self, _mock_svg):
        with patch("push_image.push_image", return_value=True), \
             patch.object(monitor.config, "DATA_DIR", MagicMock()):
            mock_path = MagicMock()
            monitor.config.DATA_DIR.__truediv__ = MagicMock(return_value=mock_path)

            result = monitor.push_alert("beardos", ["test failure"])
            assert result is True

    @patch("monitor.generate_svg", return_value="<svg/>")
    def test_returns_true_on_sms_fallback_success(self, _mock_svg):
        with patch("push_image.push_image", return_value=False), \
             patch("sms.send_to_owner") as mock_sms, \
             patch.object(monitor.config, "DATA_DIR", MagicMock()):
            mock_path = MagicMock()
            monitor.config.DATA_DIR.__truediv__ = MagicMock(return_value=mock_path)

            result = monitor.push_alert("beardos", ["test failure"])
            assert result is True
            mock_sms.assert_called_once()

    @patch("monitor.generate_svg", return_value="<svg/>")
    def test_returns_false_when_all_delivery_fails(self, _mock_svg):
        with patch("push_image.push_image", return_value=False), \
             patch("sms.send_to_owner", side_effect=Exception("SMS broken")), \
             patch.object(monitor.config, "DATA_DIR", MagicMock()):
            mock_path = MagicMock()
            monitor.config.DATA_DIR.__truediv__ = MagicMock(return_value=mock_path)

            result = monitor.push_alert("beardos", ["test failure"])
            assert result is False


class TestStateCleanup:
    """Tests for state cleanup running on every execution."""

    def test_cleanup_runs_even_with_no_failures(self):
        """Stale state entries are cleaned up even when all checks pass."""
        stale_entry = {"old_failure": time.time() - 100000}  # >24h old

        with patch("monitor.check_daemon", return_value=None), \
             patch("monitor.check_postgres", return_value=None), \
             patch("monitor.check_redis", return_value=None), \
             patch("monitor.check_backup_freshness", return_value=None), \
             patch("monitor.check_restore_freshness", return_value=None), \
             patch("monitor.check_peer", return_value=None), \
             patch("monitor.load_state", return_value=stale_entry), \
             patch("monitor.save_state") as mock_save:

            monitor.main()

            # save_state should be called with cleaned (empty) state
            mock_save.assert_called_once()
            saved_state = mock_save.call_args[0][0]
            assert len(saved_state) == 0

    def test_cleanup_preserves_recent_entries(self):
        """Recent state entries survive cleanup."""
        recent_entry = {"recent_failure": time.time() - 100}  # 100s old

        with patch("monitor.check_daemon", return_value=None), \
             patch("monitor.check_postgres", return_value=None), \
             patch("monitor.check_redis", return_value=None), \
             patch("monitor.check_backup_freshness", return_value=None), \
             patch("monitor.check_restore_freshness", return_value=None), \
             patch("monitor.check_peer", return_value=None), \
             patch("monitor.load_state", return_value=recent_entry), \
             patch("monitor.save_state") as mock_save:

            monitor.main()

            mock_save.assert_called_once()
            saved_state = mock_save.call_args[0][0]
            assert "recent_failure" in saved_state


class TestLoadSaveStatePostgres:
    """Tests for PostgreSQL-backed state persistence."""

    def test_load_state_returns_dict(self):
        """load_state queries monitor_state table and returns key->value dict."""
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [
            {"key": "failure_a", "value": 1000.0},
            {"key": "failure_b", "value": 2000.0},
        ]

        with patch("monitor.db.get_conn") as mock_get:
            mock_get.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_get.return_value.__exit__ = MagicMock(return_value=False)

            result = monitor.load_state()

        assert result == {"failure_a": 1000.0, "failure_b": 2000.0}

    def test_load_state_returns_empty_on_error(self):
        """load_state returns {} if DB query fails."""
        with patch("monitor.db.get_conn", side_effect=Exception("DB down")):
            result = monitor.load_state()
        assert result == {}

    def test_save_state_upserts_rows(self):
        """save_state UPSERTs each key/value into monitor_state."""
        mock_conn = MagicMock()

        with patch("monitor.db.get_conn") as mock_get:
            mock_get.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_get.return_value.__exit__ = MagicMock(return_value=False)

            monitor.save_state({"failure_a": 1000.0, "failure_b": 2000.0})

        assert mock_conn.execute.call_count == 2
        # Verify UPSERT SQL
        sql = mock_conn.execute.call_args_list[0][0][0]
        assert "INSERT INTO monitor_state" in sql
        assert "ON CONFLICT" in sql

    def test_save_state_handles_db_error(self):
        """save_state logs warning but doesn't crash on DB error."""
        with patch("monitor.db.get_conn", side_effect=Exception("DB down")):
            # Should not raise
            monitor.save_state({"failure_a": 1000.0})


class TestHasCriticalFailure:
    """Tests for critical failure detection."""

    def test_daemon_is_critical(self):
        assert monitor.has_critical_failure(["daemon"]) is True

    def test_postgres_is_critical(self):
        assert monitor.has_critical_failure(["postgres"]) is True

    def test_redis_is_not_critical(self):
        assert monitor.has_critical_failure(["redis"]) is False

    def test_peer_is_not_critical(self):
        assert monitor.has_critical_failure(["peer"]) is False

    def test_mixed_with_critical(self):
        assert monitor.has_critical_failure(["redis", "daemon", "peer"]) is True

    def test_empty_list(self):
        assert monitor.has_critical_failure([]) is False


class TestCleanupState:
    """Tests for the cleanup_state helper."""

    def test_removes_old_entries(self):
        state = {"old": time.time() - 100000, "new": time.time() - 100}
        result = monitor.cleanup_state(state)
        assert "old" not in result
        assert "new" in result

    def test_empty_state(self):
        assert monitor.cleanup_state({}) == {}
