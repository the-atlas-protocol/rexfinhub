"""DB-based email recipient management.

Replaces text file recipient loading with database queries.
Per-report recipient lists: each report type has its own list.
Works on both local and Render (shared DB).
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# Valid list types for email recipients
VALID_LIST_TYPES = {"daily", "weekly", "li", "income", "flow", "autocall", "private", "intelligence", "screener", "pipeline"}


def get_recipients(db: Session, list_type: str) -> list[str]:
    """Get active recipients for a specific report type."""
    from webapp.models import EmailRecipient

    if list_type not in VALID_LIST_TYPES:
        log.warning("Invalid list_type: %s", list_type)
        return []

    rows = db.query(EmailRecipient.email).filter(
        EmailRecipient.list_type == list_type,
        EmailRecipient.is_active == True,
    ).all()
    return [r.email for r in rows]


def get_private_recipients(db: Session) -> list[str]:
    """Get active private (BCC) recipients."""
    return get_recipients(db, "private")


def add_recipient(db: Session, email: str, list_type: str, added_by: str = "admin") -> bool:
    """Add a recipient to a list. Returns False if already exists."""
    from webapp.models import EmailRecipient

    if list_type not in VALID_LIST_TYPES:
        return False

    email = email.strip().lower()
    if not email:
        return False

    existing = db.query(EmailRecipient).filter(
        EmailRecipient.email == email,
        EmailRecipient.list_type == list_type,
    ).first()

    if existing:
        if not existing.is_active:
            existing.is_active = True
            existing.added_at = datetime.utcnow()
            existing.added_by = added_by
            db.commit()
            return True
        return False  # Already active

    db.add(EmailRecipient(
        email=email,
        list_type=list_type,
        is_active=True,
        added_by=added_by,
    ))
    db.commit()
    return True


def remove_recipient(db: Session, email: str, list_type: str) -> bool:
    """Remove (deactivate) a recipient from a list."""
    from webapp.models import EmailRecipient

    email = email.strip().lower()
    row = db.query(EmailRecipient).filter(
        EmailRecipient.email == email,
        EmailRecipient.list_type == list_type,
    ).first()

    if row:
        row.is_active = False
        db.commit()
        return True
    return False


def get_all_recipients_by_list(db: Session) -> dict[str, list[str]]:
    """Get all active recipients grouped by list type."""
    from webapp.models import EmailRecipient

    result = {lt: [] for lt in VALID_LIST_TYPES}
    rows = db.query(EmailRecipient).filter(EmailRecipient.is_active == True).all()
    for r in rows:
        if r.list_type in result:
            result[r.list_type].append(r.email)
    return result


def seed_from_text_files(db: Session) -> dict[str, int]:
    """One-time migration: import recipients from text files into DB.

    Only imports if the DB table is empty. Safe to call multiple times.
    """
    from webapp.models import EmailRecipient
    from pathlib import Path

    existing = db.query(EmailRecipient).count()
    if existing > 0:
        return {"skipped": existing, "imported": 0}

    config_dir = Path(__file__).resolve().parent.parent.parent / "config"
    file_map = {
        "email_recipients.txt": "daily",  # Main list gets "daily" by default
        "email_recipients_private.txt": "private",
        "autocall_recipients.txt": "autocall",
    }

    imported = 0
    for filename, list_type in file_map.items():
        path = config_dir / filename
        # Try .bak files if main files are empty/cleared
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        emails = [l.strip() for l in lines if l.strip() and not l.startswith("#")]
        if not emails:
            bak = config_dir / f"{filename}.bak"
            if bak.exists():
                lines = bak.read_text(encoding="utf-8").splitlines()
                emails = [l.strip() for l in lines if l.strip() and not l.startswith("#")]

        for email in emails:
            db.add(EmailRecipient(
                email=email.lower(),
                list_type=list_type,
                is_active=True,
                added_by="seed",
            ))
            # For the main list, also add to weekly/li/income/flow
            if list_type == "daily":
                for extra in ["weekly", "li", "income", "flow"]:
                    db.add(EmailRecipient(
                        email=email.lower(),
                        list_type=extra,
                        is_active=True,
                        added_by="seed",
                    ))
                    imported += 1
            imported += 1

    if imported:
        db.commit()
        log.info("Seeded %d email recipients from text files", imported)

    return {"imported": imported}
