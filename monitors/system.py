"""System monitor — disk, logs, cron freshness, GPU temp, Portage sync.

Does NOT duplicate monitor.py's daemon/postgres/redis/backup/peer checks.
Focuses on system health indicators that monitor.py doesn't cover.
"""

import logging
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from monitors import BaseMonitor, Finding

log = logging.getLogger("aria.monitors.system")


class SystemMonitor(BaseMonitor):
    domain = "system"
    schedule_minutes = 15
    waking_only = False  # system issues should be caught even during quiet hours

    def run(self) -> list[Finding]:
        findings = []

        try:
            findings.extend(self._check_disk_usage())
        except Exception as e:
            log.error("[MONITOR] system disk check failed: %s", e)

        try:
            findings.extend(self._check_log_sizes())
        except Exception as e:
            log.error("[MONITOR] system log size check failed: %s", e)

        try:
            findings.extend(self._check_cron_freshness())
        except Exception as e:
            log.error("[MONITOR] system cron check failed: %s", e)

        try:
            findings.extend(self._check_gpu_temp())
        except Exception as e:
            log.error("[MONITOR] system GPU check failed: %s", e)

        try:
            findings.extend(self._check_portage_sync())
        except Exception as e:
            log.error("[MONITOR] system Portage check failed: %s", e)

        return findings

    def _check_disk_usage(self) -> list[Finding]:
        """Check disk usage on root filesystem."""
        usage = shutil.disk_usage("/")
        pct = (usage.used / usage.total) * 100
        free_gb = usage.free / (1024 ** 3)

        if pct > 95:
            return [Finding(
                domain=self.domain,
                summary=f"CRITICAL: Disk {pct:.1f}% full — only {free_gb:.1f}GB free",
                urgency="urgent",
                check_key="disk_critical",
                data={"pct": round(pct, 1), "free_gb": round(free_gb, 1)},
            )]
        elif pct > 90:
            return [Finding(
                domain=self.domain,
                summary=f"Disk {pct:.1f}% full — {free_gb:.1f}GB free",
                urgency="normal",
                check_key="disk_warning",
                data={"pct": round(pct, 1), "free_gb": round(free_gb, 1)},
            )]
        return []

    def _check_log_sizes(self) -> list[Finding]:
        """Check if log files are growing unbounded."""
        import config
        findings = []
        log_dir = getattr(config, "LOGS_DIR", Path("/home/user/aria/logs"))

        for log_file in ["tick.log", "monitor.log"]:
            path = log_dir / log_file
            if path.exists():
                size_mb = path.stat().st_size / (1024 * 1024)
                if size_mb > 100:
                    findings.append(Finding(
                        domain=self.domain,
                        summary=f"Log file {log_file} is {size_mb:.0f}MB — consider rotation",
                        urgency="low",
                        check_key=f"log_large_{log_file}",
                        data={"file": log_file, "size_mb": round(size_mb, 1)},
                    ))
        return findings

    def _check_cron_freshness(self) -> list[Finding]:
        """Check if tick.py is running (via tick_state timestamp)."""
        import db

        try:
            with db.get_conn() as conn:
                row = conn.execute(
                    "SELECT value FROM tick_state WHERE key = 'last_tick_run'"
                ).fetchone()
        except Exception:
            return []

        if not row:
            return []

        try:
            last_run = datetime.fromisoformat(row["value"])
            minutes_stale = (datetime.now() - last_run).total_seconds() / 60

            if minutes_stale > 10:
                return [Finding(
                    domain=self.domain,
                    summary=f"tick.py may not be running — last activity "
                            f"{minutes_stale:.0f} minutes ago",
                    urgency="urgent" if minutes_stale > 30 else "normal",
                    check_key="cron_stale",
                    data={"minutes_stale": round(minutes_stale, 1)},
                )]
        except (ValueError, TypeError):
            pass

        return []

    def _check_gpu_temp(self) -> list[Finding]:
        """Check GPU temperature via nvidia-smi (if available)."""
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return []

            temp = int(result.stdout.strip())
            if temp > 85:
                return [Finding(
                    domain=self.domain,
                    summary=f"GPU temperature {temp}°C — throttling likely above 90°C",
                    urgency="urgent",
                    check_key="gpu_temp_high",
                    data={"temp_c": temp},
                )]
            elif temp > 78:
                return [Finding(
                    domain=self.domain,
                    summary=f"GPU temperature {temp}°C — elevated",
                    urgency="low",
                    check_key="gpu_temp_elevated",
                    data={"temp_c": temp},
                )]
        except FileNotFoundError:
            pass  # nvidia-smi not installed
        except Exception:
            pass

        return []

    def _check_portage_sync(self) -> list[Finding]:
        """Check when Gentoo Portage tree was last synced."""
        import config as _config

        findings = []
        hostname = getattr(_config, "HOST_NAME", "unknown")

        # Check local Portage sync
        findings.extend(self._check_portage_host(
            "/var/db/repos/gentoo/metadata/timestamp.chk",
            hostname,
        ))

        # Check remote host (slappy or beardos) via SSH
        peer_host = "slappy" if hostname == "beardos" else "beardos"
        try:
            result = subprocess.run(
                ["ssh", "-p", "80", "-o", "ConnectTimeout=3",
                 "-o", "StrictHostKeyChecking=no",
                 peer_host,
                 "stat -c %Y /var/db/repos/gentoo/metadata/timestamp.chk"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                mtime = int(result.stdout.strip())
                days_ago = (time.time() - mtime) / 86400
                if days_ago > 30:
                    findings.append(Finding(
                        domain=self.domain,
                        summary=f"{peer_host}: Gentoo not updated in {days_ago:.0f} days "
                                f"— run 'emerge --sync && emerge -avuDN @world'",
                        urgency="urgent",
                        check_key=f"portage_stale_{peer_host}",
                        data={"host": peer_host, "days_ago": round(days_ago, 1)},
                    ))
                elif days_ago > 14:
                    findings.append(Finding(
                        domain=self.domain,
                        summary=f"{peer_host}: Gentoo not updated in {days_ago:.0f} days",
                        urgency="normal",
                        check_key=f"portage_stale_{peer_host}",
                        data={"host": peer_host, "days_ago": round(days_ago, 1)},
                    ))
        except Exception:
            pass  # peer unreachable, not a system monitor concern

        return findings

    def _check_portage_host(self, timestamp_path: str,
                            hostname: str) -> list[Finding]:
        """Check Portage sync freshness for a specific host."""
        if not os.path.exists(timestamp_path):
            return []

        mtime = os.path.getmtime(timestamp_path)
        days_ago = (time.time() - mtime) / 86400

        if days_ago > 30:
            return [Finding(
                domain=self.domain,
                summary=f"{hostname}: Gentoo not updated in {days_ago:.0f} days "
                        f"— run 'emerge --sync && emerge -avuDN @world'",
                urgency="urgent",
                check_key=f"portage_stale_{hostname}",
                data={"host": hostname, "days_ago": round(days_ago, 1)},
            )]
        elif days_ago > 14:
            return [Finding(
                domain=self.domain,
                summary=f"{hostname}: Gentoo not updated in {days_ago:.0f} days",
                urgency="normal",
                check_key=f"portage_stale_{hostname}",
                data={"host": hostname, "days_ago": round(days_ago, 1)},
            )]

        return []
