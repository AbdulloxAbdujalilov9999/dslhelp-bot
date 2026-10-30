from __future__ import annotations

import datetime as dt
import os
import tempfile

from openpyxl import Workbook
from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .. import config
from ..db import session_scope
from ..models import Driver, Load, PTIRequest, PTIRequestDriver, Ticket, ensure_aware, utcnow

PERIODS = {
    "today": ("Bugun", dt.timedelta(days=1)),
    "week": ("Hafta", dt.timedelta(days=7)),
    "month": ("Oy", dt.timedelta(days=30)),
}


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = [InlineKeyboardButton(label, callback_data=f"export:period:{key}") for key, (label, _) in PERIODS.items()]
    await update.effective_message.reply_text("Davrni tanlang:", reply_markup=InlineKeyboardMarkup([rows]))


def _build_workbook(tickets: list[Ticket], loads: list[Load], pti_rows: list[tuple]) -> str:
    wb = Workbook()

    ws = wb.active
    ws.title = "Tickets"
    ws.append(["ID", "Bo'lim", "Turi", "Driver", "Holat", "Yaratildi", "Yopildi"])
    for t, driver_name in tickets:
        ws.append([t.id, t.department, t.category, driver_name, t.status, str(t.created_at), str(t.closed_at or "")])

    ws2 = wb.create_sheet("Loads")
    ws2.append(["ID", "Load#", "Broker", "Driver", "Holat", "PU", "DEL", "Yaratildi"])
    for ld, driver_name in loads:
        ws2.append(
            [ld.id, ld.load_number, ld.broker, driver_name, ld.status, ld.pu_address, ld.del_address, str(ld.created_at)]
        )

    ws3 = wb.create_sheet("PTI")
    ws3.append(["Request ID", "Driver", "Holat", "So'rov vaqti"])
    for rd, driver_name, requested_at in pti_rows:
        ws3.append([rd.pti_request_id, driver_name, rd.status, str(requested_at)])

    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    wb.save(path)
    return path


async def cb_export_period(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    period_key = query.data.split(":")[2]
    label, delta = PERIODS[period_key]
    since = utcnow() - delta
    await query.answer("Tayyorlanmoqda...")

    async with session_scope() as session:
        ticket_rows = (
            await session.execute(select(Ticket).where(Ticket.created_at >= since).order_by(Ticket.id))
        ).scalars().all()
        tickets = []
        for t in ticket_rows:
            driver = await session.get(Driver, t.driver_id)
            tickets.append((t, driver.full_name if driver else ""))

        load_rows = (
            await session.execute(select(Load).where(Load.created_at >= since).order_by(Load.id))
        ).scalars().all()
        loads = []
        for ld in load_rows:
            driver = await session.get(Driver, ld.driver_id)
            loads.append((ld, driver.full_name if driver else ""))

        pti_rd_rows = (
            await session.execute(
                select(PTIRequestDriver, PTIRequest.created_at)
                .join(PTIRequest, PTIRequestDriver.pti_request_id == PTIRequest.id)
                .where(PTIRequest.created_at >= since)
                .order_by(PTIRequestDriver.id)
            )
        ).all()
        pti_rows = []
        for rd, requested_at in pti_rd_rows:
            driver = await session.get(Driver, rd.driver_id)
            pti_rows.append((rd, driver.full_name if driver else "", requested_at))

    path = _build_workbook(tickets, loads, pti_rows)
    try:
        with open(path, "rb") as f:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=f,
                filename=f"dslhelp_export_{period_key}.xlsx",
                caption=f"📊 Export: {label}",
            )
    finally:
        os.remove(path)
