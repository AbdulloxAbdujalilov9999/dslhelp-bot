from __future__ import annotations
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .. import config
from ..core import build_menu, clear_state, get_driver_by_user, get_or_create_group, get_state, set_state
from ..db import session_scope
from ..tickets import create_ticket

SUBMIT_ROW = [("✅ Submit", "intake:submit"), ("❌ Bekor qilish", "intake:cancel")]


async def show_category_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    dept = config.DEPARTMENTS[code]
    rows = [(cat, f"intake:cat:{code}:{i}") for i, cat in enumerate(dept.categories)]
    await update.effective_message.reply_text(
        f"{dept.name_uz}: turini tanlang.", reply_markup=build_menu(rows, columns=2)
    )


async def cb_choose_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, _, code, idx_str = query.data.split(":")
    category = config.DEPARTMENTS[code].categories[int(idx_str)]

    chat = update.effective_chat
    user = update.effective_user
    async with session_scope() as session:
        group = await get_or_create_group(session, chat.id, chat.title or "")
        driver = await get_driver_by_user(session, user.id, group.id) if group.kind == "driver" else None
        if driver is None:
            await query.answer("Avval ro'yxatdan o'ting: /register Ism Familiya", show_alert=True)
            return

        if code == "accounting" and category == "Receipt":
            from . import accounting

            await query.answer()
            await accounting.start_receipt_flow(update, context, driver)
            return

        await set_state(
            session, user.id, {"type": "intake", "department": code, "category": category, "text": "", "photos": []}
        )

    await query.answer()
    await query.message.reply_text(
        f"✍️ {category}: matn yozing va/yoki rasm yuboring, so'ngra Submit bosing.",
        reply_markup=build_menu(SUBMIT_ROW, columns=2),
    )


async def handle_intake_text(update: Update, context: ContextTypes.DEFAULT_TYPE, state: dict) -> None:
    text = update.effective_message.text or ""
    state["text"] = (state.get("text", "") + "\n" + text).strip()
    async with session_scope() as session:
        await set_state(session, update.effective_user.id, state)
    await update.effective_message.reply_text("📝 Qabul qilindi. Davom eting yoki Submit bosing.")


async def handle_intake_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, state: dict) -> None:
    photo_file_id = update.effective_message.photo[-1].file_id
    state.setdefault("photos", []).append(photo_file_id)
    async with session_scope() as session:
        await set_state(session, update.effective_user.id, state)
    await update.effective_message.reply_text("📷 Rasm qabul qilindi. Davom eting yoki Submit bosing.")


async def cb_submit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat = update.effective_chat
    user = update.effective_user

    async with session_scope() as session:
        state = await get_state(session, user.id)
        if not state or state.get("type") != "intake":
            await query.answer("Sessiya tugagan, qaytadan boshlang.", show_alert=True)
            return
        if not state.get("text") and not state.get("photos"):
            await query.answer("Matn yoki rasm kiriting.", show_alert=True)
            return

        group = await get_or_create_group(session, chat.id, chat.title or "")
        driver = await get_driver_by_user(session, user.id, group.id)
        media = [(fid, "photo") for fid in state.get("photos", [])]
        ticket = await create_ticket(
            session,
            context,
            department=state["department"],
            category=state["category"],
            driver=driver,
            driver_group=group,
            text=state.get("text", ""),
            media=media,
        )
        await clear_state(session, user.id)

    await query.answer("Yuborildi.")
    if ticket:
        await query.edit_message_text(f"✅ Yuborildi, ticket #{ticket.id}.")
    else:
        await query.edit_message_text("⚠️ Bo'lim guruhi ulanmagan, admin bilan bog'laning.")


async def cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    async with session_scope() as session:
        await clear_state(session, update.effective_user.id)
    await query.answer("Bekor qilindi.")
    await query.edit_message_text("❌ Bekor qilindi.")
