from __future__ import annotations
import datetime as dt
import glob
import json
import logging
import os
from zoneinfo import ZoneInfo

from sqlalchemy import select

from telegram.ext import ContextTypes

from . import config
from .core import get_department_group, local_today, notify_admins
from .db import session_scope
from .models import Base, Document, Driver, Group, Load, PTIRequest, PTIRequestDriver, Ticket, Truck, ensure_aware, utcnow
from .tickets import due_for_reminder, send_reminder

logger = logging.getLogger(__name__)

LATE_GRACE = dt.timedelta(minutes=15)


async def reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        tickets = await due_for_reminder(session)
        for ticket in tickets:
            try:
                await send_reminder(session, context, ticket)
            except Exception:
                logger.exception("Failed to send reminder for ticket %s", ticket.id)


async def appointment_reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """PU/DEL appointment reminders (2h, 30m before) and late alerts to Dispatch.

    Each threshold fires at most once per load (tracked by boolean flags), so this
    is safe to run on any poll cadence rather than needing exact-minute scheduling.
    """
    now = utcnow()
    async with session_scope() as session:
        loads = (await session.execute(select(Load).where(Load.status != "invoiced"))).scalars().all()
        dispatch_group = await get_department_group(session, "dispatch")

        for load in loads:
            driver = await session.get(Driver, load.driver_id)
            driver_group = await session.get(Group, load.driver_group_id)

            if load.pu_at is not None and load.status == "assigned":
                await _check_appointment(
                    context, now, ensure_aware(load.pu_at), "PU", load.pu_address,
                    driver, driver_group, dispatch_group,
                    load, "pu_reminder_2h", "pu_reminder_30m", "pu_late_alert",
                )
            if load.del_at is not None and load.status in ("assigned", "picked_up"):
                await _check_appointment(
                    context, now, ensure_aware(load.del_at), "DEL", load.del_address,
                    driver, driver_group, dispatch_group,
                    load, "del_reminder_2h", "del_reminder_30m", "del_late_alert",
                )


async def _check_appointment(
    context, now, at, label, address, driver, driver_group, dispatch_group, load,
    flag_2h, flag_30m, flag_late,
) -> None:
    from .modules.dispatch import _format_local

    remaining = at - now
    if remaining > dt.timedelta(hours=2):
        return

    if remaining > dt.timedelta(0):
        if remaining <= dt.timedelta(minutes=30) and not getattr(load, flag_30m):
            await context.bot.send_message(
                chat_id=driver_group.chat_id,
                text=f"⏰ {driver.first_name}, {label} 30 daqiqadan keyin! Manzil: {address}",
            )
            setattr(load, flag_30m, True)
        elif not getattr(load, flag_2h):
            await context.bot.send_message(
                chat_id=driver_group.chat_id,
                text=f"⏰ {driver.first_name}, {label} {_format_local(at)}da, 2 soat qoldi. Manzil: {address}",
            )
            setattr(load, flag_2h, True)
    elif -remaining >= LATE_GRACE and not getattr(load, flag_late):
        if dispatch_group is not None:
            await context.bot.send_message(
                chat_id=dispatch_group.chat_id,
                text=f"🔴 LATE: {driver.full_name} hali {label} qilmadi (reja: {_format_local(at)}).",
            )
        setattr(load, flag_late, True)


async def _build_daily_report(session) -> str:
    today = local_today()

    active_loads = (
        await session.execute(select(Load).where(Load.status != "invoiced"))
    ).scalars().all()

    open_tickets = (await session.execute(select(Ticket).where(Ticket.status != "done"))).scalars().all()
    open_by_dept: dict[str, int] = {}
    for t in open_tickets:
        open_by_dept[t.department] = open_by_dept.get(t.department, 0) + 1

    active_drivers = (await session.execute(select(Driver).where(Driver.active.is_(True)))).scalars().all()

    # Filter "today" in Python, not SQL: SQLite drops tzinfo on stored datetimes, so a
    # SQL-side comparison against an aware bind parameter becomes an unreliable string
    # compare (see ensure_aware()). Fetch requests and filter with aware-safe Python dates.
    all_requests = (await session.execute(select(PTIRequest))).scalars().all()
    today_request_ids = {r.id for r in all_requests if ensure_aware(r.created_at).astimezone(ZoneInfo(config.TIMEZONE)).date() == today}
    all_rds = (await session.execute(select(PTIRequestDriver))).scalars().all()
    today_rds = [rd for rd in all_rds if rd.pti_request_id in today_request_ids]
    approved_today_driver_ids = {rd.driver_id for rd in today_rds if rd.status == "approved"}
    no_response_driver_ids = {rd.driver_id for rd in today_rds if rd.status == "pending"}
    pti_missing = [d for d in active_drivers if d.id not in approved_today_driver_ids]
    no_response = [d for d in active_drivers if d.id in no_response_driver_ids]

    cutoff = today + dt.timedelta(days=config.DOC_WARNING_DAYS)
    expiring_docs = (
        await session.execute(select(Document).where(Document.expires_on <= cutoff))
    ).scalars().all()

    lines = [f"📊 Kunlik hisobot — {today.isoformat()}", "", f"🚚 Faol loadlar: {len(active_loads)}", ""]

    lines.append("📋 Ochiq ticketlar (bo'lim bo'yicha):")
    if open_by_dept:
        for code, count in open_by_dept.items():
            lines.append(f"  {config.DEPARTMENTS[code].name_uz}: {count}")
    else:
        lines.append("  yo'q")
    lines.append("")

    lines.append(f"🔧 PTI qilmagan driverlar (bugun): {len(pti_missing)}")
    if pti_missing:
        lines.append("  " + ", ".join(d.full_name for d in pti_missing[:20]))
    lines.append("")

    lines.append(f"🤐 Javob bermagan driverlar (PTI so'roviga): {len(no_response)}")
    if no_response:
        lines.append("  " + ", ".join(d.full_name for d in no_response[:20]))
    lines.append("")

    lines.append(f"📄 Muddati yaqinlashgan hujjatlar: {len(expiring_docs)}")
    if expiring_docs:
        for doc in expiring_docs[:20]:
            driver = await session.get(Driver, doc.driver_id)
            days_left = (doc.expires_on - today).days
            lines.append(f"  {driver.full_name} — {doc.doc_type} ({days_left} kun)")

    return "\n".join(lines)


async def pm_reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    today = local_today()
    async with session_scope() as session:
        maintenance_group = await get_department_group(session, "maintenance")
        if maintenance_group is None:
            return
        trucks = (await session.execute(select(Truck))).scalars().all()
        for truck in trucks:
            if truck.pm_alert_sent:
                continue
            miles_due = truck.mileage - truck.last_pm_mileage >= truck.pm_interval_miles
            days_due = truck.last_pm_date is not None and (today - truck.last_pm_date).days >= truck.pm_interval_days
            if not (miles_due or days_due):
                continue
            try:
                await context.bot.send_message(
                    chat_id=maintenance_group.chat_id,
                    text=(
                        f"🛠 PM muddati keldi: Truck {truck.number} "
                        f"(milya: {truck.mileage}, oxirgi PM: {truck.last_pm_mileage})."
                    ),
                )
            except Exception:
                logger.exception("Failed to send PM reminder for truck %s", truck.number)
            truck.pm_alert_sent = True


async def daily_report_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        text = await _build_daily_report(session)
        dispatch_group = await get_department_group(session, "dispatch")
        if dispatch_group is not None:
            try:
                await context.bot.send_message(chat_id=dispatch_group.chat_id, text=text)
            except Exception:
                logger.exception("Failed to send daily report to dispatch group")
    await notify_admins(context, text)


def _json_default(value):
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    raise TypeError(f"Not JSON serializable: {value!r}")


async def backup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Daily cross-backend DB backup: dumps every table to one JSON file.

    Works the same for SQLite and Postgres (no pg_dump/sqlite3-CLI dependency),
    since it goes through the same SQLAlchemy layer the bot already uses.
    """
    os.makedirs(config.BACKUP_DIR, exist_ok=True)
    dump: dict[str, list[dict]] = {}

    async with session_scope() as session:
        for table in Base.metadata.sorted_tables:
            result = await session.execute(select(table))
            dump[table.name] = [dict(row._mapping) for row in result]

    filename = f"backup-{local_today().isoformat()}.json"
    path = os.path.join(config.BACKUP_DIR, filename)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dump, f, default=_json_default, ensure_ascii=False, indent=2)
        logger.info("Backup written to %s", path)
    except OSError:
        logger.exception("Failed to write backup to %s", path)
        return

    cutoff = utcnow() - dt.timedelta(days=config.BACKUP_RETENTION_DAYS)
    for old_path in glob.glob(os.path.join(config.BACKUP_DIR, "backup-*.json")):
        try:
            mtime = dt.datetime.fromtimestamp(os.path.getmtime(old_path), tz=dt.timezone.utc)
            if mtime < cutoff:
                os.remove(old_path)
        except OSError:
            logger.exception("Failed to prune old backup %s", old_path)
