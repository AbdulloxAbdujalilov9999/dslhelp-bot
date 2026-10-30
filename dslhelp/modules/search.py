from __future__ import annotations

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .. import config
from ..core import get_driver_truck, local_today
from ..db import session_scope
from ..models import Document, Driver, Group, Load, PTIRequestDriver, Ticket
from ..tickets import STATUS_LABELS, render_ticket_text
from .dispatch import STATUS_LABELS_UZ, _format_local

MAX_PER_CATEGORY = 10


async def _driver_profile_text(session, driver: Driver) -> str:
    group = await session.get(Group, driver.group_id)
    lines = [f"👤 {driver.full_name}", f"Guruh: {group.title or group.chat_id}"]

    truck = await get_driver_truck(session, driver.id)
    if truck:
        lines.append(f"Truck: {truck.number}")

    if driver.last_lat is not None and driver.last_lon is not None:
        lines.append(f"Oxirgi lokatsiya: https://maps.google.com/?q={driver.last_lat},{driver.last_lon}")

    load = (
        await session.execute(
            select(Load).where(Load.driver_id == driver.id, Load.status != "invoiced").order_by(Load.id.desc())
        )
    ).scalars().first()
    if load:
        label = load.load_number or f"#{load.id}"
        lines.append(f"Load: {label} — {STATUS_LABELS_UZ.get(load.status, load.status)}")

    open_tickets = (
        await session.execute(select(Ticket).where(Ticket.driver_id == driver.id, Ticket.status != "done"))
    ).scalars().all()
    if open_tickets:
        lines.append("Ochiq ticketlar:")
        for t in open_tickets:
            lines.append(f"  #{t.id} {config.DEPARTMENTS[t.department].name_uz} — {t.category or ''} [{STATUS_LABELS[t.status]}]")

    last_pti = (
        await session.execute(
            select(PTIRequestDriver).where(PTIRequestDriver.driver_id == driver.id).order_by(PTIRequestDriver.id.desc())
        )
    ).scalars().first()
    if last_pti:
        lines.append(f"Oxirgi PTI holati: {last_pti.status}")

    docs = (await session.execute(select(Document).where(Document.driver_id == driver.id))).scalars().all()
    if docs:
        today = local_today()
        lines.append("Hujjatlar:")
        for doc in docs:
            days_left = (doc.expires_on - today).days
            lines.append(f"  {doc.doc_type} — {doc.expires_on.isoformat()} ({days_left} kun)")

    return "\n".join(lines)


async def _find_drivers_by_name(session, name: str) -> list[Driver]:
    drivers = (await session.execute(select(Driver).where(Driver.active.is_(True)))).scalars().all()
    exact = [d for d in drivers if d.full_name.lower() == name.lower()]
    if exact:
        return exact
    return [d for d in drivers if name.lower() in d.full_name.lower()]


async def cmd_driver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Foydalanish: /driver <ism>")
        return
    name = " ".join(context.args)

    async with session_scope() as session:
        matches = await _find_drivers_by_name(session, name)
        if not matches:
            await update.effective_message.reply_text(f"Driver topilmadi: {name}")
            return
        if len(matches) > 1:
            rows = [InlineKeyboardButton(d.full_name, callback_data=f"search:driverprofile:{d.id}") for d in matches]
            await update.effective_message.reply_text(
                "Bir nechta driver topildi:", reply_markup=InlineKeyboardMarkup([[r] for r in rows])
            )
            return
        text = await _driver_profile_text(session, matches[0])
    await update.effective_message.reply_text(text)


async def cb_driver_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    driver_id = int(query.data.split(":")[2])
    await query.answer()
    async with session_scope() as session:
        driver = await session.get(Driver, driver_id)
        if driver is None:
            await query.message.reply_text("Driver topilmadi.")
            return
        text = await _driver_profile_text(session, driver)
    await query.message.reply_text(text)


async def cmd_find(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Foydalanish: /find <matn>")
        return
    needle = " ".join(context.args).lower()

    rows: list[InlineKeyboardButton] = []
    async with session_scope() as session:
        drivers = (await session.execute(select(Driver).where(Driver.active.is_(True)))).scalars().all()
        for d in drivers:
            if needle in d.full_name.lower():
                rows.append(InlineKeyboardButton(f"👤 {d.full_name}", callback_data=f"search:driverprofile:{d.id}"))
        rows = rows[:MAX_PER_CATEGORY]

        tickets = (await session.execute(select(Ticket))).scalars().all()
        ticket_rows = [
            InlineKeyboardButton(f"🎫 #{t.id} {t.category or t.department}", callback_data=f"search:ticket:{t.id}")
            for t in tickets
            if needle in (t.text or "").lower() or needle in (t.category or "").lower()
        ][:MAX_PER_CATEGORY]

        loads = (await session.execute(select(Load))).scalars().all()
        load_rows = [
            InlineKeyboardButton(
                f"🚚 {ld.load_number or '#' + str(ld.id)}", callback_data=f"search:load:{ld.id}"
            )
            for ld in loads
            if needle in (ld.load_number or "").lower()
            or needle in (ld.broker or "").lower()
            or needle in (ld.pod_trailer or "").lower()
        ][:MAX_PER_CATEGORY]

    all_rows = rows + ticket_rows + load_rows
    if not all_rows:
        await update.effective_message.reply_text("Hech narsa topilmadi.")
        return

    await update.effective_message.reply_text(
        f"Natijalar ({len(all_rows)}):", reply_markup=InlineKeyboardMarkup([[r] for r in all_rows])
    )


async def cb_search_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    ticket_id = int(query.data.split(":")[2])
    await query.answer()
    async with session_scope() as session:
        ticket = await session.get(Ticket, ticket_id)
        if ticket is None:
            await query.message.reply_text("Ticket topilmadi.")
            return
        driver = await session.get(Driver, ticket.driver_id)
        text = render_ticket_text(ticket, driver)
    await query.message.reply_text(text)


async def cb_search_load(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    load_id = int(query.data.split(":")[2])
    await query.answer()
    async with session_scope() as session:
        load = await session.get(Load, load_id)
        if load is None:
            await query.message.reply_text("Load topilmadi.")
            return
        driver = await session.get(Driver, load.driver_id)
        lines = [
            f"🚚 Load {load.load_number or '#' + str(load.id)}",
            f"Driver: {driver.full_name}",
            f"Broker: {load.broker or '-'}",
            f"PU: {load.pu_address or '-'} ({_format_local(load.pu_at)})",
            f"DEL: {load.del_address or '-'} ({_format_local(load.del_at)})",
            f"Holat: {STATUS_LABELS_UZ.get(load.status, load.status)}",
        ]
        if load.pod_trailer:
            lines.append(f"Trailer/POD: {load.pod_trailer}")
        text = "\n".join(lines)
    await query.message.reply_text(text)
