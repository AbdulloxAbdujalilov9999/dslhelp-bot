from __future__ import annotations
import datetime as dt

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .. import config
from ..core import (
    clear_state,
    get_department_group,
    get_driver_any_group,
    get_or_create_group,
    local_today,
    log_action,
    require_staff_or_admin,
    set_state,
)
from ..db import session_scope
from ..models import Document, Driver, Group

DOC_TYPES = {"CDL", "MEDICAL"}
ONBOARDING_SEQUENCE = ["CDL", "MEDICAL"]


async def cmd_doc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 3:
        await update.effective_message.reply_text(
            "Foydalanish: /doc Ism Familiya TUR YYYY-MM-DD  (TUR: CDL, MEDICAL)"
        )
        return

    *name_parts, doc_type, date_str = context.args
    name = " ".join(name_parts)
    doc_type = doc_type.upper()
    if doc_type not in DOC_TYPES:
        await update.effective_message.reply_text(f"Noto'g'ri tur: {doc_type}. Ruxsat etilgan: {', '.join(DOC_TYPES)}")
        return
    try:
        expires_on = dt.date.fromisoformat(date_str)
    except ValueError:
        await update.effective_message.reply_text("Sana formati: YYYY-MM-DD")
        return

    async with session_scope() as session:
        group = await get_or_create_group(session, update.effective_chat.id, update.effective_chat.title or "")
        if group.kind != "hr":
            await update.effective_message.reply_text("Bu buyruq faqat HR guruhida ishlaydi.")
            return
        if not await require_staff_or_admin(update, session, "hr"):
            return

        result = await session.execute(select(Driver).where(Driver.active.is_(True)))
        drivers = result.scalars().all()
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
        session.add(Document(driver_id=driver.id, doc_type=doc_type, expires_on=expires_on))
        await log_action(session, update.effective_user.id, update.effective_user.full_name, "doc", f"{driver.full_name}:{doc_type}")

    await update.effective_message.reply_text(f"✅ {driver.full_name}: {doc_type} — {expires_on.isoformat()} saqlandi.")


async def cmd_docs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        result = await session.execute(select(Document).order_by(Document.expires_on))
        docs = result.scalars().all()
        if not docs:
            await update.effective_message.reply_text("Hujjatlar yo'q.")
            return
        lines = []
        today = local_today()
        for doc in docs[:50]:
            driver = await session.get(Driver, doc.driver_id)
            days_left = (doc.expires_on - today).days
            flag = "⚠️" if days_left <= config.DOC_WARNING_DAYS else ""
            lines.append(f"{flag} {driver.full_name} — {doc.doc_type} — {doc.expires_on.isoformat()} ({days_left} kun)")

    await update.effective_message.reply_text("\n".join(lines))


async def cb_docs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_docs(update, context)


async def run_daily_doc_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    today = local_today()
    cutoff = today + dt.timedelta(days=config.DOC_WARNING_DAYS)

    async with session_scope() as session:
        result = await session.execute(select(Document).where(Document.expires_on <= cutoff))
        docs = result.scalars().all()
        if not docs:
            return
        lines = ["📄 Muddati yaqinlashgan hujjatlar:"]
        for doc in docs:
            driver = await session.get(Driver, doc.driver_id)
            days_left = (doc.expires_on - today).days
            lines.append(f"- {driver.full_name}: {doc.doc_type} — {doc.expires_on.isoformat()} ({days_left} kun qoldi)")
        text = "\n".join(lines)

        for dept_code in ("hr", "safety"):
            group = await get_department_group(session, dept_code)
            if group is not None:
                try:
                    await context.bot.send_message(chat_id=group.chat_id, text=text)
                except Exception:
                    pass


# --------------------------------------------------------------- onboarding --

async def advance_onboarding(session, context: ContextTypes.DEFAULT_TYPE, driver: Driver) -> bool:
    """Prompt for the next missing onboarding doc, or clear state if none remain.

    Returns True if a new step was started, False if onboarding is complete.
    Rejected docs don't count as "have", so a HR reject naturally re-triggers collection.
    """
    docs = (await session.execute(select(Document).where(Document.driver_id == driver.id))).scalars().all()
    have_types = {d.doc_type for d in docs if d.status != "rejected"}
    missing = [t for t in ONBOARDING_SEQUENCE if t not in have_types]
    if not missing:
        await clear_state(session, driver.telegram_user_id)
        return False

    await set_state(session, driver.telegram_user_id, {"type": "onboarding", "doc_type": missing[0], "awaiting": "photo"})
    driver_group = await session.get(Group, driver.group_id)
    try:
        await context.bot.send_message(
            chat_id=driver_group.chat_id, text=f"📋 {missing[0]} rasmini yuboring (hujjat rasmi)."
        )
    except Exception:
        pass
    return True


async def handle_onboarding_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, state: dict) -> None:
    file_id = update.effective_message.photo[-1].file_id
    state["photo_file_id"] = file_id
    state["awaiting"] = "date"
    async with session_scope() as session:
        await set_state(session, update.effective_user.id, state)
    await update.effective_message.reply_text(f"📅 {state['doc_type']} amal qilish muddati (YYYY-MM-DD):")


async def handle_onboarding_date_text(update: Update, context: ContextTypes.DEFAULT_TYPE, state: dict) -> None:
    text = (update.effective_message.text or "").strip()
    try:
        expires_on = dt.date.fromisoformat(text)
    except ValueError:
        await update.effective_message.reply_text("Sana formati: YYYY-MM-DD. Qayta kiriting:")
        return

    user_id = update.effective_user.id
    async with session_scope() as session:
        driver = await get_driver_any_group(session, user_id)
        if driver is None:
            await clear_state(session, user_id)
            return

        doc_type = state["doc_type"]
        photo_file_id = state.get("photo_file_id")
        doc = Document(driver_id=driver.id, doc_type=doc_type, expires_on=expires_on, file_id=photo_file_id, status="pending")
        session.add(doc)
        await session.flush()
        doc_id = doc.id
        driver_name = driver.full_name

        hr_group = await get_department_group(session, "hr")
        if hr_group is not None:
            keyboard = InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton("✅ Approve", callback_data=f"onboard:approve:{doc_id}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"onboard:reject:{doc_id}"),
                ]]
            )
            caption = f"📋 {driver_name}: {doc_type} — {expires_on.isoformat()}"
            if photo_file_id:
                await context.bot.send_photo(chat_id=hr_group.chat_id, photo=photo_file_id, caption=caption, reply_markup=keyboard)
            else:
                await context.bot.send_message(chat_id=hr_group.chat_id, text=caption, reply_markup=keyboard)

        started_next = await advance_onboarding(session, context, driver)

    if started_next:
        await update.effective_message.reply_text(f"✅ {doc_type} qabul qilindi.")
    else:
        await update.effective_message.reply_text("✅ Barcha hujjatlar yuborildi. HR tasdiqlashini kuting.")


async def cb_onboard_decide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, action, doc_id_str = query.data.split(":")
    doc_id = int(doc_id_str)

    async with session_scope() as session:
        if not await require_staff_or_admin(update, session, "hr"):
            await query.answer("⛔ Faqat HR xodimlari uchun.", show_alert=True)
            return
        doc = await session.get(Document, doc_id)
        if doc is None:
            await query.answer("Topilmadi.", show_alert=True)
            return
        driver = await session.get(Driver, doc.driver_id)
        driver_group = await session.get(Group, driver.group_id)

        if action == "approve":
            doc.status = "approved"
            await context.bot.send_message(
                chat_id=driver_group.chat_id, text=f"✅ {driver.first_name}, {doc.doc_type} tasdiqlandi."
            )
        else:
            doc.status = "rejected"
            await context.bot.send_message(
                chat_id=driver_group.chat_id, text=f"❌ {driver.first_name}, {doc.doc_type} rad etildi, qaytadan yuboring."
            )
            await advance_onboarding(session, context, driver)

        await log_action(
            session, update.effective_user.id, update.effective_user.full_name,
            f"onboard_{action}", f"{driver.full_name}:{doc.doc_type}",
        )

    await query.answer("Qayd etildi.")
