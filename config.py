from __future__ import annotations
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///dslhelp.db")
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x}
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Tashkent")
REMINDER_POLL_SECONDS = int(os.environ.get("REMINDER_POLL_SECONDS", "60"))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
PORT = os.environ.get("PORT")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5")
GOOGLE_SHEETS_CREDENTIALS = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")

DAILY_REPORT_HOUR = int(os.environ.get("DAILY_REPORT_HOUR", "20"))
DAILY_REPORT_MINUTE = int(os.environ.get("DAILY_REPORT_MINUTE", "0"))
DOC_WARNING_HOUR = 9
DOC_WARNING_MINUTE = 0
DOC_WARNING_DAYS = int(os.environ.get("DOC_WARNING_DAYS", "14"))

STATE_TTL_MINUTES = 60

PTI_PAGE_SIZE = 8
URGENT_REMINDER_MINUTES = 2

BACKUP_DIR = os.environ.get("BACKUP_DIR", "backups")
BACKUP_RETENTION_DAYS = int(os.environ.get("BACKUP_RETENTION_DAYS", "14"))
BACKUP_HOUR = int(os.environ.get("BACKUP_HOUR", "3"))
BACKUP_MINUTE = int(os.environ.get("BACKUP_MINUTE", "0"))

WEB_PANEL_TOKEN = os.environ.get("WEB_PANEL_TOKEN", "")
WEB_PANEL_PORT = int(os.environ.get("WEB_PANEL_PORT", "8090"))


@dataclass(frozen=True)
class Department:
    code: str
    name_uz: str
    emoji: str
    reminder_minutes: int
    # categories offered in the generic "intake" flow (text + photo + submit).
    # Empty for departments that have their own dedicated module (dispatch, maintenance).
    categories: tuple = field(default_factory=tuple)


# Single source of truth for departments. Adding a department = add one entry here.
DEPARTMENTS: dict[str, Department] = {
    "dispatch": Department(
        code="dispatch",
        name_uz="🚛 Dispatch",
        emoji="🚛",
        reminder_minutes=10,
    ),
    "maintenance": Department(
        code="maintenance",
        name_uz="🔧 Maintenance",
        emoji="🔧",
        reminder_minutes=5,
    ),
    "eld": Department(
        code="eld",
        name_uz="📡 ELD",
        emoji="📡",
        reminder_minutes=5,
        categories=("ELD malfunction", "Log edit", "Device issue"),
    ),
    "safety": Department(
        code="safety",
        name_uz="🛡 Safety",
        emoji="🛡",
        reminder_minutes=5,
        categories=("Incident", "Inspection"),
    ),
    "accounting": Department(
        code="accounting",
        name_uz="💰 Accounting",
        emoji="💰",
        reminder_minutes=120,
        categories=("Settlement", "Receipt"),
    ),
    "hr": Department(
        code="hr",
        name_uz="👥 HR",
        emoji="👥",
        reminder_minutes=240,
        categories=("Document update", "Vacation", "Other question"),
    ),
}

DEPARTMENT_ORDER = ["dispatch", "maintenance", "eld", "safety", "accounting", "hr"]

INTAKE_DEPARTMENTS = {code for code, d in DEPARTMENTS.items() if d.categories}

# When a ticket of (department, category) is created, forward a copy to these department groups.
# "ELD malfunction" -> Dispatch + Safety; Incident/Inspection -> Dispatch (ASOSIY QOIDALAR / BO'LIMLARARO XABAR).
CATEGORY_FORWARD_RULES: dict[tuple[str, str], tuple[str, ...]] = {
    ("eld", "ELD malfunction"): ("dispatch", "safety"),
    ("safety", "Incident"): ("dispatch",),
    ("safety", "Inspection"): ("dispatch",),
}
