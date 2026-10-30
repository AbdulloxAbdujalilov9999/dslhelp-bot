from __future__ import annotations
from zoneinfo import ZoneInfo

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .. import config
from ..core import (
    build_menu,
    get_driver_by_user,
    get_driver_truck,
    get_or_create_group,
    get_state,
    local_today,
    log_action,
    require_staff_or_admin,
    set_state,
    clear_state,
)
from ..db import session_scope
from ..models import (
    CostEntry,
    Driver,
    Group,
    PTIPhoto,
    PTIRequest,
    PTIRequestDriver,
    Ticket,
    Truck,
    ensure_aware,
    utcnow,
)
from ..tickets import create_ticket, find_open_ticket, register_done_hook, set_ticket_status

SERVICE_CATEGORY = "Service so'rovi"

DRIVER_MENU_ROWS = [("🔧 Service so'rovi (/t)", "maintenance:t_hint")]


async def show_driver_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "🔧 Maintenance:\n/t <muammo tavsifi> — service so'rovi yuborish",
        reply_markup=build_menu(DRIVER_MENU_ROWS, columns=1),
    )


async def cb_t_hint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("Foydalanish: /t <muammo tavsifi>")


async def cmd_t(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    text = " ".join(context.args) if context.args else "(tavsif yo'q)"

    async with session_scope() as session:
        group = await get_or_create_group(session, chat.id, chat.title or "")
        if group.kind != "driver":
            await update.effective_message.reply_text("Bu buyruq faqat driver guruhida ishlaydi.")
            return
        driver = await get_driver_by_user(session, user.id, group.id)
        if driver is None:
            await update.effective_message.reply_text("Avval ro'yxatdan o'ting: /register Ism Familiya")
            return

        existing = await find_open_ticket(session, "maintenance", driver.id, SERVICE_CATEGORY)
        if existing is not None:
            await update.effective_message.reply_text(
                f"⚠️ Siz allaqachon so'ragansiz (ticket #{existing.id} hali ochiq)."
            )
            return

        truck = await get_driver_truck(session, driver.id)
        ticket = await create_ticket(
            session,
            context,
            department="maintenance",
            category=SERVICE_CATEGORY,
            driver=driver,
            driver_group=group,
            text=text,
            truck_id=truck.id if truck else None,
        )
    if ticket:
        await update.effective_message.reply_text(f"✅ So'rov yuborildi (ticket #{ticket.id}).")


# ---------------------------------------------------------------- PTI flow --

async def cb_pti_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    async with session_scope() as session:
        await set_state(session, query.from_user.id, {"type": "pti_select", "selected": [], "page": 0})
    await _render_pti_select(update, context, page=0, selected=[])


async def _driver_list(session) -> list[Driver]:
    result = await session.execute(select(Driver).where(Driver.active.is_(True)).order_by(Driver.first_name))
    return list(result.scalars().all())


async def _render_pti_select(update, context, page: int, selected: list[int], edit: bool = False) -> None:
    async with session_scope() as session:
        drivers = await _driver_list(session)

    page_size = config.PTI_PAGE_SIZE
    start = page * page_size
    page_drivers = drivers[start : start + page_size]
    rows = []
    for d in page_drivers:
        mark = "☑️" if d.id in selected else "⬜️"
        rows.append(InlineKeyboardButton(f"{mark} {d.full_name}", callback_data=f"pti:toggle:{d.id}"))
    keyboard = [[r] for r in rows]

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Oldingi", callback_data=f"pti:page:{page - 1}"))
    if start + page_size < len(drivers):
        nav.append(InlineKeyboardButton("➡️ Keyingi", callback_data=f"pti:page:{page + 1}"))
    if nav:
        keyboard.append(nav)
    keyboard.append(
        [
            InlineKeyboardButton(f"✅ Yuborish ({len(selected)})", callback_data="pti:send"),
            InlineKeyboardButton("❌ Bekor qilish", callback_data="pti:cancel"),
        ]
    )
    text = "PTI uchun driverlarni tanlang:"
    markup = InlineKeyboardMarkup(keyboard)
    if edit:
        await update.callback_query.edit_message_text(text, reply_markup=markup)
    else:
        await update.effective_message.reply_text(text, reply_markup=markup)


async def cb_pti_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    driver_id = int(query.data.split(":")[2])
    async with session_scope() as session:
        state = await get_state(session, query.from_user.id) or {"type": "pti_select", "selected": [], "page": 0}
        selected = set(state.get("selected", []))
        if driver_id in selected:
            selected.discard(driver_id)
        else:
            selected.add(driver_id)
        state["selected"] = list(selected)
        await set_state(session, query.from_user.id, state)
    await query.answer()
    await _render_pti_select(update, context, page=state.get("page", 0), selected=state["selected"], edit=True)


async def cb_pti_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    page = int(query.data.split(":")[2])
    async with session_scope() as session:
        state = await get_state(session, query.from_user.id) or {"type": "pti_select", "selected": [], "page": 0}
        state["page"] = page
        await set_state(session, query.from_user.id, state)
    await query.answer()
    await _render_pti_select(update, context, page=page, selected=state.get("selected", []), edit=True)


async def cb_pti_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    async with session_scope() as session:
        await clear_state(session, query.from_user.id)
    await query.answer("Bekor qilindi.")
    await query.edit_message_text("Bekor qilindi.")


async def cb_pti_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat = update.effective_chat

    async with session_scope() as session:
        state = await get_state(session, query.from_user.id)
        selected = state.get("selected", []) if state else []
        if not selected:
            await query.answer("Hech kim tanlanmadi.", show_alert=True)
            return

        group = await get_or_create_group(session, chat.id, chat.title or "")
        pti_request = PTIRequest(maintenance_group_id=group.id)
        session.add(pti_request)
        await session.flush()

        for driver_id in selected:
            truck = await get_driver_truck(session, driver_id)
            rd = PTIRequestDriver(
                pti_request_id=pti_request.id,
                driver_id=driver_id,
                truck_id=truck.id if truck else None,
                status="pending",
            )
            session.add(rd)
            await session.flush()
            driver = await session.get(Driver, driver_id)
            driver_group = await session.get(Group, driver.group_id)
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("✅ Done", callback_data=f"pti:done:{rd.id}")]]
            )
            try:
                sent = await context.bot.send_message(
                    chat_id=driver_group.chat_id,
                    text=f"🔧 {driver.first_name}, PTI rasmlarini yuboring, so'ngra Done bosing.",
                    reply_markup=keyboard,
                )
                rd.prompt_message_id = sent.message_id
            except Exception:
                pass
            await set_state(
                session, driver.telegram_user_id, {"type": "pti_submit", "request_driver_id": rd.id}
            )

        await clear_state(session, query.from_user.id)
        req_id = pti_request.id

    await query.answer("Yuborildi.")
    await query.edit_message_text(f"✅ PTI so'rovi yuborildi ({len(selected)} driver). Request #{req_id}")


async def handle_pti_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, state: dict) -> None:
    """Called by the generic photo dispatcher when the sender has an active pti_submit state."""
    rd_id = state["request_driver_id"]
    photo_file_id = update.effective_message.photo[-1].file_id
    async with session_scope() as session:
        session.add(PTIPhoto(pti_request_driver_id=rd_id, file_id=photo_file_id))
    await update.effective_message.reply_text("📷 Rasm qabul qilindi. Tugatgach, Done bosing.")


async def cb_pti_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    rd_id = int(query.data.split(":")[2])

    async with session_scope() as session:
        rd = await session.get(PTIRequestDriver, rd_id)
        if rd is None:
            await query.answer("Topilmadi.", show_alert=True)
            return
        if query.from_user.id != (await session.get(Driver, rd.driver_id)).telegram_user_id:
            await query.answer("Bu sizga tegishli emas.", show_alert=True)
            return

        photos = (
            await session.execute(
                select(PTIPhoto).where(PTIPhoto.pti_request_driver_id == rd_id, PTIPhoto.active.is_(True))
            )
        ).scalars().all()
        if not photos:
            await query.answer("Avval rasm yuboring.", show_alert=True)
            return

        rd.status = "submitted"
        driver = await session.get(Driver, rd.driver_id)
        pti_request = await session.get(PTIRequest, rd.pti_request_id)
        maintenance_group = await session.get(Group, pti_request.maintenance_group_id)
        await clear_state(session, query.from_user.id)

        approve_kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"pti:approve:{rd.id}"),
                    InlineKeyboardButton("🔁 Redo", callback_data=f"pti:redo:{rd.id}"),
                ]
            ]
        )
        await context.bot.send_photo(
            chat_id=maintenance_group.chat_id,
            photo=photos[0].file_id,
            caption=f"🔧 PTI: {driver.full_name}",
            reply_markup=approve_kb,
        )
        for extra in photos[1:]:
            await context.bot.send_photo(chat_id=maintenance_group.chat_id, photo=extra.file_id)

    await query.answer("Yuborildi.")
    try:
        await query.edit_message_text(f"📷 PTI yuborildi: {driver.full_name}")
    except Exception:
        pass


async def cb_pti_approve_redo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action, rd_id_str = query.data.split(":")[1], query.data.split(":")[2]
    rd_id = int(rd_id_str)

    async with session_scope() as session:
        rd = await session.get(PTIRequestDriver, rd_id)
        driver = await session.get(Driver, rd.driver_id)
        driver_group = await session.get(Group, driver.group_id)

        if action == "approve":
            rd.status = "approved"
            await context.bot.send_message(
                chat_id=driver_group.chat_id, text=f"✅ {driver.first_name}, PTI tasdiqlandi."
            )
        else:
            rd.status = "redo"
            for photo in (
                await session.execute(select(PTIPhoto).where(PTIPhoto.pti_request_driver_id == rd_id))
            ).scalars().all():
                photo.active = False
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done", callback_data=f"pti:done:{rd.id}")]])
            await context.bot.send_message(
                chat_id=driver_group.chat_id,
                text=f"🔁 {driver.first_name}, PTI rasmlarini qayta yuboring.",
                reply_markup=keyboard,
            )
            await set_state(
                session, driver.telegram_user_id, {"type": "pti_submit", "request_driver_id": rd.id}
            )

    await query.answer("Qayd etildi.")


async def cb_pti_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    icons = {"pending": "⏳", "submitted": "📷", "approved": "✅", "redo": "🔁"}

    async with session_scope() as session:
        result = await session.execute(select(PTIRequest).order_by(PTIRequest.id.desc()))
        latest = result.scalars().first()
        if latest is None:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="PTI so'rovi topilmadi.")
            return
        rows = (
            await session.execute(select(PTIRequestDriver).where(PTIRequestDriver.pti_request_id == latest.id))
        ).scalars().all()
        lines = [f"PTI so'rovi #{latest.id} holati:"]
        for rd in rows:
            driver = await session.get(Driver, rd.driver_id)
            lines.append(f"{icons.get(rd.status, '•')} {driver.full_name} — {rd.status}")

    await context.bot.send_message(chat_id=update.effective_chat.id, text="\n".join(lines))


# --------------------------------------------------------------- truck tracking --

async def _require_maintenance_group(update: Update, session):
    group = await get_or_create_group(session, update.effective_chat.id, update.effective_chat.title or "")
    if group.kind != "maintenance":
        await update.effective_message.reply_text("Bu buyruq faqat Maintenance guruhida ishlaydi.")
        return None
    return group


async def _render_truck_history(session, truck: Truck) -> str:
    driver_name = "-"
    if truck.driver_id:
        driver = await session.get(Driver, truck.driver_id)
        driver_name = driver.full_name if driver else "-"

    lines = [
        f"🚛 Truck {truck.number}",
        f"Driver: {driver_name}",
        f"Milya: {truck.mileage}",
        f"Oxirgi PM: {truck.last_pm_mileage} milya"
        + (f", {truck.last_pm_date.isoformat()}" if truck.last_pm_date else ""),
        f"Keyingi PM: {truck.last_pm_mileage + truck.pm_interval_miles} milyada",
    ]

    service_tickets = (
        await session.execute(
            select(Ticket).where(Ticket.truck_id == truck.id).order_by(Ticket.id.desc()).limit(10)
        )
    ).scalars().all()
    if service_tickets:
        lines.append("Service tarixi:")
        for t in service_tickets:
            cost = (await session.execute(select(CostEntry).where(CostEntry.ticket_id == t.id))).scalars().first()
            cost_text = f" — {cost.work_type} ({cost.amount:,.0f})" if cost else ""
            lines.append(f"  #{t.id} [{t.status}]{cost_text}")

    pti_rows = (
        await session.execute(
            select(PTIRequestDriver)
            .where(PTIRequestDriver.truck_id == truck.id)
            .order_by(PTIRequestDriver.id.desc())
            .limit(10)
        )
    ).scalars().all()
    if pti_rows:
        lines.append("PTI tarixi:")
        for rd in pti_rows:
            lines.append(f"  so'rov #{rd.pti_request_id} — {rd.status}")

    return "\n".join(lines)


async def cmd_truck(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Foydalanish: /truck <raqami>")
        return
    number = context.args[0].upper()

    async with session_scope() as session:
        if await _require_maintenance_group(update, session) is None:
            return
        truck = (await session.execute(select(Truck).where(Truck.number == number))).scalar_one_or_none()
        if truck is None:
            session.add(Truck(number=number))
            await update.effective_message.reply_text(f"✅ Yangi truck qo'shildi: {number}")
            return
        text = await _render_truck_history(session, truck)
    await update.effective_message.reply_text(text)


async def cmd_assigntruck(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = update.effective_message.text or ""
    _, _, rest = raw.partition(" ")
    parts = [p.strip() for p in rest.split("|")] if rest.strip() else []
    if len(parts) < 2:
        await update.effective_message.reply_text("Foydalanish: /assigntruck <truck raqami> | <driver ism>")
        return
    number, name = parts[0].upper(), parts[1]

    async with session_scope() as session:
        if await _require_maintenance_group(update, session) is None:
            return
        if not await require_staff_or_admin(update, session, "maintenance"):
            return
        truck = (await session.execute(select(Truck).where(Truck.number == number))).scalar_one_or_none()
        if truck is None:
            await update.effective_message.reply_text(f"Truck topilmadi: {number}. Avval /truck {number} bilan qo'shing.")
            return

        drivers = (await session.execute(select(Driver).where(Driver.active.is_(True)))).scalars().all()
        matches = [d for d in drivers if d.full_name.lower() == name.lower()]
        if not matches:
            matches = [d for d in drivers if name.lower() in d.full_name.lower()]
        if not matches:
            await update.effective_message.reply_text(f"Driver topilmadi: {name}")
            return
        if len(matches) > 1:
            names = "\n".join(f"- {d.full_name}" for d in matches)
            await update.effective_message.reply_text(f"Bir nechta driver topildi:\n{names}")
            return

        truck.driver_id = matches[0].id
        driver_name = matches[0].full_name
        await log_action(session, update.effective_user.id, update.effective_user.full_name, "assigntruck", f"{number} -> {driver_name}")

    await update.effective_message.reply_text(f"✅ {number} endi {driver_name}ga tayinlandi.")


async def cmd_mileage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.effective_message.reply_text("Foydalanish: /mileage <truck raqami> <masofa>")
        return
    number = context.args[0].upper()
    try:
        mileage = int(context.args[1])
    except ValueError:
        await update.effective_message.reply_text("Masofa butun son bo'lishi kerak.")
        return

    async with session_scope() as session:
        if await _require_maintenance_group(update, session) is None:
            return
        if not await require_staff_or_admin(update, session, "maintenance"):
            return
        truck = (await session.execute(select(Truck).where(Truck.number == number))).scalar_one_or_none()
        if truck is None:
            await update.effective_message.reply_text(f"Truck topilmadi: {number}")
            return
        truck.mileage = mileage
        await log_action(session, update.effective_user.id, update.effective_user.full_name, "mileage", f"{number} -> {mileage}")

    await update.effective_message.reply_text(f"✅ {number} milyaji yangilandi: {mileage}")


async def cmd_pmdone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Foydalanish: /pmdone <truck raqami>")
        return
    number = context.args[0].upper()

    async with session_scope() as session:
        if await _require_maintenance_group(update, session) is None:
            return
        if not await require_staff_or_admin(update, session, "maintenance"):
            return
        truck = (await session.execute(select(Truck).where(Truck.number == number))).scalar_one_or_none()
        if truck is None:
            await update.effective_message.reply_text(f"Truck topilmadi: {number}")
            return
        truck.last_pm_mileage = truck.mileage
        truck.last_pm_date = local_today()
        truck.pm_alert_sent = False
        await log_action(session, update.effective_user.id, update.effective_user.full_name, "pmdone", number)

    await update.effective_message.reply_text(f"✅ {number}: PM bugun bajarilgan deb belgilandi.")


# --------------------------------------------------------- cost-on-close + /costs --

async def _maintenance_done_hook(update: Update, context: ContextTypes.DEFAULT_TYPE, session, ticket: Ticket) -> bool:
    query = update.callback_query
    await set_state(session, query.from_user.id, {"type": "maintenance_close", "ticket_id": ticket.id})
    await query.answer()
    await query.message.reply_text(
        "🔧 Ishni yopishdan oldin ish turi va summani kiriting (masalan: 'Moy almashtirish | 450000')."
    )
    return True


register_done_hook("maintenance", _maintenance_done_hook)


async def handle_close_cost_text(update: Update, context: ContextTypes.DEFAULT_TYPE, state: dict) -> None:
    text = update.effective_message.text or ""
    parts = [p.strip() for p in text.split("|")]
    work_type = parts[0] if parts else text.strip()
    amount = 0.0
    if len(parts) > 1:
        try:
            amount = float(parts[1].replace(",", ""))
        except ValueError:
            amount = 0.0

    ticket_id = state["ticket_id"]
    async with session_scope() as session:
        ticket = await session.get(Ticket, ticket_id)
        if ticket is None:
            await clear_state(session, update.effective_user.id)
            return
        session.add(CostEntry(ticket_id=ticket.id, truck_id=ticket.truck_id, work_type=work_type, amount=amount))
        await clear_state(session, update.effective_user.id)
        await set_ticket_status(session, context, ticket_id, "done", update.effective_user.full_name)

    await update.effective_message.reply_text(f"✅ Xarajat qayd etildi: {work_type} — {amount:,.0f}. Ticket yopildi.")


async def cmd_costs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    month_start = local_today().replace(day=1)
    async with session_scope() as session:
        entries = (await session.execute(select(CostEntry))).scalars().all()
        this_month = [
            e for e in entries
            if ensure_aware(e.created_at).astimezone(ZoneInfo(config.TIMEZONE)).date() >= month_start
        ]
        if not this_month:
            await update.effective_message.reply_text("Bu oyda xarajat yozilmagan.")
            return

        by_truck: dict[str, float] = {}
        total = 0.0
        for e in this_month:
            truck_label = "-"
            if e.truck_id:
                truck = await session.get(Truck, e.truck_id)
                truck_label = truck.number if truck else "-"
            by_truck[truck_label] = by_truck.get(truck_label, 0.0) + e.amount
            total += e.amount

        lines = [f"💰 Xarajatlar ({month_start.isoformat()} dan):"]
        for truck_label, amount in by_truck.items():
            lines.append(f"  {truck_label}: {amount:,.0f}")
        lines.append(f"Jami: {total:,.0f}")

    await update.effective_message.reply_text("\n".join(lines))

    await context.bot.send_message(chat_id=update.effective_chat.id, text="\n".join(lines))
