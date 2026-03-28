"""Tests for monitors package — domain monitors and finding infrastructure.

SAFETY: All store operations are mocked at the MODULE level (per CLAUDE.md).
Monitors use local imports (import X inside functions), so we patch the actual
module, not monitors.health.X.
"""

from datetime import datetime, date, timedelta
from unittest.mock import patch, MagicMock

import monitors
from monitors import Finding
from monitors.health import HealthMonitor
from monitors.fitness import FitnessMonitor
from monitors.vehicle import VehicleMonitor
from monitors.legal import LegalMonitor
from monitors.system import SystemMonitor


class TestFinding:
    def test_finding_creation(self):
        f = Finding(domain="health", summary="test", urgency="normal", check_key="test_key")
        assert f.domain == "health"
        assert f.data == {}

    def test_finding_with_data(self):
        f = Finding(domain="system", summary="disk full", urgency="urgent",
                    check_key="disk", data={"pct": 95})
        assert f.data["pct"] == 95


class TestFingerprint:
    def test_same_inputs_same_fingerprint(self):
        fp1 = monitors._fingerprint("health", "choline_low")
        fp2 = monitors._fingerprint("health", "choline_low")
        assert fp1 == fp2

    def test_different_inputs_different_fingerprint(self):
        fp1 = monitors._fingerprint("health", "choline_low")
        fp2 = monitors._fingerprint("health", "protein_low")
        assert fp1 != fp2

    def test_fingerprint_length(self):
        fp = monitors._fingerprint("test", "key")
        assert len(fp) == 16


class TestHealthMonitor:
    @patch("nutrition_store.get_daily_totals")
    def test_choline_low_finding(self, mock_totals):
        mock_totals.return_value = {
            "item_count": 4, "choline_mg": 200, "protein_g": 120,
            "dietary_fiber_g": 30,
        }
        monitor = HealthMonitor()
        findings = monitor._check_daily_compliance(datetime.now().strftime("%Y-%m-%d"))
        assert any(f.check_key == "choline_low" for f in findings)

    @patch("nutrition_store.get_daily_totals")
    def test_no_finding_when_few_items(self, mock_totals):
        mock_totals.return_value = {
            "item_count": 1, "choline_mg": 50, "protein_g": 10,
            "dietary_fiber_g": 5,
        }
        monitor = HealthMonitor()
        findings = monitor._check_daily_compliance(datetime.now().strftime("%Y-%m-%d"))
        assert len(findings) == 0

    @patch("nutrition_store.get_net_calories")
    def test_surplus_trend(self, mock_net):
        mock_net.return_value = {"consumed": 2500, "burned": 2000, "net": 500}
        monitor = HealthMonitor()
        findings = monitor._check_calorie_activity_correlation(
            datetime.now().strftime("%Y-%m-%d"))
        assert any(f.check_key == "surplus_trend" for f in findings)


class TestFitnessMonitor:
    @patch("fitbit_store.get_resting_hr_history")
    def test_hr_trend_up(self, mock_hr):
        mock_hr.return_value = [65, 66, 64, 65, 66, 73]
        monitor = FitnessMonitor()
        findings = monitor._check_hr_trend()
        assert any(f.check_key == "hr_trend_up" for f in findings)

    @patch("fitbit_store.get_resting_hr_history")
    def test_hr_stable_no_finding(self, mock_hr):
        mock_hr.return_value = [65, 66, 64, 65, 66, 65]
        monitor = FitnessMonitor()
        findings = monitor._check_hr_trend()
        assert len(findings) == 0


class TestVehicleMonitor:
    @patch("vehicle_store.get_latest_by_type")
    def test_oil_change_overdue(self, mock_latest):
        old_date = (date.today() - timedelta(days=200)).isoformat()
        mock_latest.return_value = {
            "oil_change": {"date": old_date, "mileage": 120000},
        }
        monitor = VehicleMonitor()
        findings = monitor.run()
        assert any(f.check_key == "oil_change_overdue" for f in findings)

    @patch("vehicle_store.get_latest_by_type")
    def test_recent_service_no_finding(self, mock_latest):
        recent_date = (date.today() - timedelta(days=30)).isoformat()
        mock_latest.return_value = {
            "oil_change": {"date": recent_date, "mileage": 125000},
        }
        monitor = VehicleMonitor()
        findings = monitor.run()
        assert len(findings) == 0


class TestLegalMonitor:
    @patch("legal_store.get_upcoming_dates")
    def test_deadline_7d(self, mock_upcoming):
        future = (date.today() + timedelta(days=5)).isoformat()
        mock_upcoming.return_value = [
            {"id": "abc", "date": future, "description": "Court hearing"}
        ]
        monitor = LegalMonitor()
        findings = monitor.run()
        assert len(findings) == 1
        assert findings[0].urgency == "info"

    @patch("legal_store.get_upcoming_dates")
    def test_deadline_1d(self, mock_upcoming):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        mock_upcoming.return_value = [
            {"id": "abc", "date": tomorrow, "description": "Filing deadline"}
        ]
        monitor = LegalMonitor()
        findings = monitor.run()
        assert len(findings) == 1
        assert findings[0].urgency == "normal"

    @patch("legal_store.get_upcoming_dates")
    def test_deadline_overdue(self, mock_upcoming):
        past = (date.today() - timedelta(days=2)).isoformat()
        mock_upcoming.return_value = [
            {"id": "abc", "date": past, "description": "Missed deadline"}
        ]
        monitor = LegalMonitor()
        findings = monitor.run()
        assert len(findings) == 1
        assert findings[0].urgency == "urgent"


class TestSystemMonitor:
    @patch("shutil.disk_usage")
    def test_disk_critical(self, mock_usage):
        mock_usage.return_value = MagicMock(
            total=100_000_000_000, used=96_000_000_000, free=4_000_000_000)
        monitor = SystemMonitor()
        findings = monitor._check_disk_usage()
        assert len(findings) == 1
        assert findings[0].urgency == "urgent"

    @patch("shutil.disk_usage")
    def test_disk_ok(self, mock_usage):
        mock_usage.return_value = MagicMock(
            total=100_000_000_000, used=50_000_000_000, free=50_000_000_000)
        monitor = SystemMonitor()
        findings = monitor._check_disk_usage()
        assert len(findings) == 0

    @patch("os.path.exists", return_value=True)
    @patch("os.path.getmtime")
    def test_portage_stale_14d(self, mock_mtime, mock_exists):
        import time
        mock_mtime.return_value = time.time() - (20 * 86400)  # 20 days ago
        monitor = SystemMonitor()
        findings = monitor._check_portage_host(
            "/var/db/repos/gentoo/metadata/timestamp.chk", "beardos")
        assert len(findings) == 1
        assert findings[0].urgency == "normal"
        assert "20" in findings[0].summary

    @patch("os.path.exists", return_value=True)
    @patch("os.path.getmtime")
    def test_portage_very_stale_30d(self, mock_mtime, mock_exists):
        import time
        mock_mtime.return_value = time.time() - (45 * 86400)  # 45 days ago
        monitor = SystemMonitor()
        findings = monitor._check_portage_host(
            "/var/db/repos/gentoo/metadata/timestamp.chk", "beardos")
        assert len(findings) == 1
        assert findings[0].urgency == "urgent"
        assert "emerge" in findings[0].summary

    @patch("os.path.exists", return_value=True)
    @patch("os.path.getmtime")
    def test_portage_fresh_no_finding(self, mock_mtime, mock_exists):
        import time
        mock_mtime.return_value = time.time() - (5 * 86400)  # 5 days ago
        monitor = SystemMonitor()
        findings = monitor._check_portage_host(
            "/var/db/repos/gentoo/metadata/timestamp.chk", "beardos")
        assert len(findings) == 0
