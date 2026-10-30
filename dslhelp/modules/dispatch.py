from __future__ import annotations
import datetime as dt
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser
from sqlalchemy import select
from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import ContextTypes

from .. import config
from ..core import (
    build_menu,
    clear_state,
    get_department_group,
    get_driver_by_user,
    get_or_create_group,
    get_state,
    log_action,
    require_staff_or_admin,
    set_state,
)
from ..db import session_scope
from ..i18n import tr
from ..models import Driver, Group, Load, utcnow


def _parse_local_datetime(text: str) -> dt.datetime | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        parsed = date_parser.parse(text)
    except (ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(config.TIMEZONE))
    return parsed.astimezone(dt.timezone.utc)


def _format_local(value: dt.datetime | None) -> str:
    if value is None:
        return "-"
    return value.astimezone(ZoneInfo(config.TIMEZONE)).strftime("%Y-%m-%d %H:%M")

DRIVER_MENU_ROWS = [
    ("📄 BOL", "dispatch:bol_hint"),
    ("📦 POD", "dispatch:pod_hint"),
    ("🚚 PU", "dispatch:pu"),
    ("📍 DEL", "dispatch:del"),
    ("ℹ️ Status", "dispatch:status"),
    ("🗺 Lokatsiya", "dispatch:share_location"),
]

LOCATION_PROMPT_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("📍 Lokatsiyani yuborish", request_location=True)]],
    resize_keyboard=True,
    one_time_keyboard=True,
)


async def show_driver_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "🚛 Dispatch:\n"
        "/BOL — BOL rasm yuboring (caption qilib /BOL yozing)\n"
        "/POD — yetkazib berildi (keyin trailer raqami yoki BOBTAIL)\n"
        "/PU — picked up\n"
        "/DEL — delivered\n"
        "/status — joriy holat",
        reply_markup=build_menu(DRIVER_MENU_ROWS, columns=2),
    )


async def _current_load(session, driver) -> Load:
    result = await session.execute(
        select(Load).where(Load.driver_id == driver.id, Load.status != "invoiced").order_by(Load.id.desc())
    )
    load = result.scalars().first()
    if load is None:
        load = Load(driver_id=driver.id, driver_group_id=driver.group_id, status="assigned")
        session.add(load)
        await session.flush()
    return load


async def _require_driver(update: Update, session):
    chat = update.effective_chat
    user = update.effective_user
    group = await get_or_create_group(session, chat.id, chat.title or "")
    if group.kind != "driver":
        await update.effective_message.reply_text("Bu buyruq faqat driver guruhida ishlaydi.")
        return None, None
    driver = await get_driver_by_user(session, user.id, group.id)
    if driver is None:
        await update.effective_message.reply_text("Avval ro'yxatdan o'ting: /register Ism Familiya")
        return None, None
    return driver, group


async def _log_to_dispatch(session, context, driver, group, text: str, photo_file_id: str | None = None) -> None:
    dept_group = await get_department_group(session, "dispatch")
    if dept_group is None:
        return
    caption = f"🚛 {driver.full_name} ({group.title or group.chat_id}): {text}"
    if photo_file_id:
        await context.bot.send_photo(chat_id=dept_group.chat_id, photo=photo_file_id, caption=caption)
    else:
        await context.bot.send_message(chat_id=dept_group.chat_id, text=caption)


async def cmd_bol(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles both `/BOL` as a plain command and `/BOL` as a photo caption."""
    msg = update.effective_message
    photo_file_id = msg.photo[-1].file_id if msg.photo else None
    if photo_file_id is None:
        await msg.reply_text(tr("bol_prompt"))
        return

    async with session_scope() as session:
        driver, group = await _require_driver(update, session)
        if driver is None:
            return
        load = await _current_load(session, driver)
        load_id = load.id
        load_number_missing = not load.load_number
        load.bol_file_id = photo_file_id
        load.status = "picked_up" if load.status == "assigned" else load.status
        load.updated_at = utcnow()
        await _log_to_dispatch(session, context, driver, group, "BOL yuklandi.", photo_file_id)
        driver_lang = driver.language or "uz"
    await msg.reply_text(tr("bol_accepted", driver_lang))

    if config.ANTHROPIC_API_KEY and load_number_missing:
        await _run_bol_ocr(update, context, load_id, photo_file_id)


async def _download_photo_bytes(context: ContextTypes.DEFAULT_TYPE, file_id: str) -> bytes:
    tg_file = await context.bot.get_file(file_id)
    return bytes(await tg_file.download_as_bytearray())


async def _run_bol_ocr(update: Update, context: ContextTypes.DEFAULT_TYPE, load_id: int, photo_file_id: str) -> None:
    """Best-effort: OCR failures (network, bad JSON) are swallowed — the driver's
    BOL upload already succeeded above, this is a bonus auto-fill, never a blocker."""
    from .. import ai

    try:
        image_bytes = await _download_photo_bytes(context, photo_file_id)
    except Exception:
        return
    fields = await ai.extract_bol_fields(image_bytes)
    if not fields or not any(v is not None for v in fields.values()):
        return

    async with session_scope() as session:
        await set_state(session, update.effective_user.id, {"type": "bol_ocr_confirm", "load_id": load_id, "fields": fields})

    lines = ["🤖 BOL'dan avtomatik o'qildi:"]
    if fields.get("load_number"):
        lines.append(f"Load#: {fields['load_number']}")
    if fields.get("address"):
        lines.append(f"Manzil: {fields['address']}")
    if fields.get("amount") is not None:
        lines.append(f"Summa: {fields['amount']}")
    lines.append("Load kartochkasiga yozaymi?")

    keyboard = build_menu([("✅ Tasdiqlash", "ocr:bol:confirm"), ("❌ Bekor qilish", "ocr:bol:skip")], columns=2)
    await update.effective_message.reply_text("\n".join(lines), reply_markup=keyboard)


async def cb_ocr_bol_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    async with session_scope() as session:
        state = await get_state(session, query.from_user.id)
        if not state or state.get("type") != "bol_ocr_confirm":
            await query.answer("Sessiya tugagan.", show_alert=True)
            return
        load = await session.get(Load, state["load_id"])
        fields = state["fields"]
        if load is not None:
            if fields.get("load_number") and not load.load_number:
                load.load_number = fields["load_number"]
            if fields.get("address") and not load.pu_address:
                load.pu_address = fields["address"]
        await clear_state(session, query.from_user.id)
    await query.answer("Saqlandi.")
    await query.edit_message_text("✅ Load kartochkasiga yozildi.")


async def cb_ocr_bol_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    async with session_scope() as session:
        await clear_state(session, query.from_user.id)
    await query.answer("Bekor qilindi.")
    await query.edit_message_text("❌ Bekor qilindi.")


async def cmd_pod(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    photo_file_id = msg.photo[-1].file_id if msg.photo else None
    arg = " ".join(context.args) if context.args else None

    async with session_scope() as session:
        driver, group = await _require_driver(update, session)
        if driver is None:
            return
        load = await _current_load(session, driver)

        if photo_file_id:
            load.pod_file_id = photo_file_id
        if arg:
            load.pod_trailer = arg.upper() if arg.upper() == "BOBTAIL" else arg

        if not load.pod_trailer:
            await msg.reply_text("Trailer raqamini yoki BOBTAIL deb yozing: /POD <trailer>  yoki  /POD BOBTAIL")
            return

        load.status = "pod"
        load.updated_at = utcnow()
        detail = f"POD: {load.pod_trailer}"
        await _log_to_dispatch(session, context, driver, group, detail, load.pod_file_id)

        accounting_group = await get_department_group(session, "accounting")
        if accounting_group is not None:
            note = f"💰 POD tushdi: {driver.full_name} — {load.pod_trailer}"
            if load.bol_file_id:
                await context.bot.send_photo(chat_id=accounting_group.chat_id, photo=load.bol_file_id, caption=note)
            else:
                await context.bot.send_message(chat_id=accounting_group.chat_id, text=note)

    await msg.reply_text("✅ POD qabul qilindi va Accounting'ga yuborildi.")


async def cmd_pu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        driver, group = await _require_driver(update, session)
        if driver is None:
            return
        load = await _current_load(session, driver)
        load.status = "picked_up"
        load.updated_at = utcnow()
        await _log_to_dispatch(session, context, driver, group, "Picked up (PU).")
    await update.effective_message.reply_text("✅ PU belgilandi.")


async def cmd_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        driver, group = await _require_driver(update, session)
        if driver is None:
            return
        load = await _current_load(session, driver)
        load.status = "delivered"
        load.updated_at = utcnow()
        await _log_to_dispatch(session, context, driver, group, "Delivered (DEL).")
    await update.effective_message.reply_text("✅ DEL belgilandi.")


STATUS_LABELS_UZ = {
    "assigned": "Tayinlangan",
    "picked_up": "Picked up",
    "delivered": "Delivered",
    "pod": "POD qilindi",
    "invoiced": "Invoiced",
}


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        driver, group = await _require_driver(update, session)
        if driver is None:
            return
        load = await _current_load(session, driver)
        text = f"Joriy load holati: {STATUS_LABELS_UZ.get(load.status, load.status)}"
        if load.pod_trailer:
            text += f"\nTrailer: {load.pod_trailer}"
    await update.effective_message.reply_text(text)


async def _render_active_loads(session) -> str:
    result = await session.execute(select(Load).where(Load.status != "invoiced").order_by(Load.id.desc()).limit(30))
    loads = result.scalars().all()
    lines = []
    for load in loads:
        driver = await session.get(Driver, load.driver_id)
        label = load.load_number or f"#{load.id}"
        line = f"🚚 {label} — {driver.full_name} — {STATUS_LABELS_UZ.get(load.status, load.status)}"
        if load.broker:
            line += f" | {load.broker}"
        lines.append(line)
    return "\n".join(lines) or "Faol loadlar yo'q."


async def cb_active_loads(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    async with session_scope() as session:
        text = await _render_active_loads(session)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text)


async def cmd_loads(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        text = await _render_active_loads(session)
    await update.effective_message.reply_text(text)


NEWLOAD_USAGE = (
    "Foydalanish:\n"
    "/newload Ism Familiya | Load# | Broker | PU manzil | PU vaqt | DEL manzil | DEL vaqt"
)


async def cmd_newload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = update.effective_message.text or ""
    _, _, rest = raw.partition(" ")
    parts = [p.strip() for p in rest.split("|")] if rest.strip() else []
    if len(parts) < 2:
        await update.effective_message.reply_text(NEWLOAD_USAGE)
        return
    parts += [""] * (7 - len(parts))
    name, load_number, broker, pu_address, pu_time_text, del_address, del_time_text = parts[:7]
    pu_at = _parse_local_datetime(pu_time_text)
    del_at = _parse_local_datetime(del_time_text)
    warnings = []
    if pu_time_text and pu_at is None:
        warnings.append(f"⚠️ PU vaqti tushunilmadi: '{pu_time_text}' — eslatma ishlamaydi.")
    if del_time_text and del_at is None:
        warnings.append(f"⚠️ DEL vaqti tushunilmadi: '{del_time_text}' — eslatma ishlamaydi.")

    chat = update.effective_chat
    async with session_scope() as session:
        group = await get_or_create_group(session, chat.id, chat.title or "")
        if group.kind != "dispatch":
            await update.effective_message.reply_text("Bu buyruq faqat Dispatch guruhida ishlaydi.")
            return
        if not await require_staff_or_admin(update, session, "dispatch"):
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
            await update.effective_message.reply_text(f"Bir nechta driver topildi, aniqroq yozing:\n{names}")
            return
        driver = matches[0]

        load = Load(
            driver_id=driver.id,
            driver_group_id=driver.group_id,
            status="assigned",
            load_number=load_number,
            broker=broker,
            pu_address=pu_address,
            pu_at=pu_at,
            del_address=del_address,
            del_at=del_at,
        )
        session.add(load)
        await session.flush()
        load_id = load.id
        driver_group = await session.get(Group, driver.group_id)
        await context.bot.send_message(
            chat_id=driver_group.chat_id,
            text=(
                f"🚚 Yangi load tayinlandi: {load_number or '#' + str(load_id)}\n"
                f"Broker: {broker}\nPU: {pu_address} ({_format_local(pu_at)})\n"
                f"DEL: {del_address} ({_format_local(del_at)})"
            ),
        )
        driver_name = driver.full_name
        await log_action(
            session, update.effective_user.id, update.effective_user.full_name,
            "newload", f"Load #{load_id} -> {driver_name}",
        )

    reply = f"✅ Load #{load_id} yaratildi va {driver_name}ga tayinlandi."
    if warnings:
        reply += "\n" + "\n".join(warnings)
    await update.effective_message.reply_text(reply)


async def cb_hint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if "bol" in query.data:
        await query.message.reply_text("📄 BOL rasmini shu caption bilan yuboring: /BOL")
    else:
        await query.message.reply_text("📦 POD uchun: /POD <trailer raqami>  yoki  /POD BOBTAIL (rasm bilan ham bo'ladi)")


async def cb_share_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("📍 Lokatsiyangizni yuboring:", reply_markup=LOCATION_PROMPT_KEYBOARD)


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    loc = msg.location
    async with session_scope() as session:
        driver, group = await _require_driver(update, session)
        if driver is None:
            return
        driver.last_lat = loc.latitude
        driver.last_lon = loc.longitude
        driver.last_location_at = utcnow()
        maps_url = f"https://maps.google.com/?q={loc.latitude},{loc.longitude}"
        dispatch_group = await get_department_group(session, "dispatch")
        if dispatch_group is not None:
            await context.bot.send_message(
                chat_id=dispatch_group.chat_id, text=f"📍 {driver.full_name} lokatsiyasi: {maps_url}"
            )
    await msg.reply_text("✅ Lokatsiya qabul qilindi.", reply_markup=ReplyKeyboardRemove())
