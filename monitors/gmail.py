"""Gmail monitor — classify new emails, produce findings for important ones.

Reads from email_cache (populated by process_google_poll in tick.py).
Classifies unclassified emails via gmail_strategy, stores results in
email_classifications, and produces Finding objects for the delivery pipeline.

Does NOT fetch from Gmail API — that's handled by process_google_poll().
This monitor only does analysis on locally cached data.
"""

import logging
from datetime import datetime, timezone

from monitors import BaseMonitor, Finding

log = logging.getLogger("aria.monitors.gmail")


def _email_age_hours(email: dict) -> float | None:
    """Calculate email age in hours from its timestamp."""
    ts = email.get("timestamp")
    if not ts:
        return None
    try:
        if isinstance(ts, str):
            from dateutil.parser import parse as parse_dt
            ts = parse_dt(ts)
        if hasattr(ts, 'tzinfo') and ts.tzinfo is not None:
            now = datetime.now(timezone.utc)
        else:
            now = datetime.now()
        return (now - ts).total_seconds() / 3600
    except Exception:
        return None


class GmailMonitor(BaseMonitor):
    domain = "gmail"
    schedule_minutes = 3
    waking_only = True

    def run(self) -> list[Finding]:
        findings = []
        try:
            findings.extend(self._classify_new_emails())
        except Exception as e:
            log.error("[MONITOR] gmail classification failed: %s", e)
        return findings

    def _classify_new_emails(self) -> list[Finding]:
        """Classify emails that haven't been classified yet."""
        import gmail_store
        import gmail_strategy

        unclassified = gmail_store.get_unclassified(limit=50)
        if not unclassified:
            return []

        log.info("[MONITOR] Classifying %d new emails", len(unclassified))
        findings = []

        for email in unclassified:
            try:
                result = gmail_strategy.classify_email(email)

                gmail_store.save_classification(
                    email_id=email["id"],
                    tier=result.tier,
                    classification=result.classification,
                    confidence=result.confidence,
                    reason=result.reason,
                    category=result.category,
                )

                # Produce findings for important/urgent/actionable emails
                if result.classification in ("important", "urgent", "actionable"):
                    # Age gate: only create findings for emails < 24h old
                    age = _email_age_hours(email)
                    if age is not None and age > 24:
                        log.info("[MONITOR] Skipping finding for old email %s (%.1fh old)",
                                 email.get("id", "?"), age)
                        continue

                    sender = email.get("from_name") or email.get("from_address", "?")
                    subject = email.get("subject") or "(no subject)"

                    # Use shared check_keys for proper category routing
                    if result.priority == 1 or result.classification == "urgent":
                        check_key = "email_urgent"
                        urgency = "urgent"
                    else:
                        check_key = "email_important"
                        urgency = "normal"

                    findings.append(Finding(
                        domain="gmail",
                        summary=f"Email from {sender}: {subject}",
                        urgency=urgency,
                        check_key=check_key,
                        data={
                            "email_id": email["id"],
                            "from": sender,
                            "subject": subject,
                            "classification": result.classification,
                            "tier": result.tier,
                            "priority": result.priority,
                        },
                    ))
            except Exception as e:
                log.error("[MONITOR] Failed to classify email %s: %s",
                          email.get("id", "?"), e)

        if findings:
            log.info("[MONITOR] %d important emails found", len(findings))

        return findings
