from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from .. import ai, config
from ..core import build_menu, clear_state, get_driver_by_user, get_or_create_group, get_state, set_state
from ..db import session_scope
from ..tickets import create_ticket

MIN_TEXT_LENGTH = 8


async def try_auto_route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called by the generic text fallback when a driver sends free text with no
    active flow. Guesses a department+category via Claude and asks for confirmation
    before creating anything — never creates a ticket silently."""
    text = (update.effective_message.text or "").strip()
    if len(text) < MIN_TEXT_LENGTH:
        return

    chat = update.effective_chat
    user = update.effective_user
    async with session_scope() as session:
        group = await get_or_create_group(session, chat.id, chat.title or "")
        if group.kind != "driver":
            return
        driver = await get_driver_by_user(session, user.id, group.id)
        if driver is None:
            return

    route = await ai.classify_department(text)
    if route is None:
        return

    async with session_scope() as session:
        await set_state(
            session,
            user.id,
            {"type": "auto_route_confirm", "department": route["department"], "category": route["category"], "text": text},
        )

    dept_name = config.DEPARTMENTS[route["department"]].name_uz
    keyboard = build_menu([("✅ Ha", "autoroute:confirm"), ("❌ Yo'q", "autoroute:cancel")], columns=2)
    await update.effective_message.reply_text(
        f"🤖 Bu xabar menimcha {dept_name} ({route['category']})ga oid. Ticket ochaymi?",
        reply_markup=keyboard,
    )


async def cb_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = query.from_user
    chat = update.effective_chat

    async with session_scope() as session:
        state = await get_state(session, user.id)
        if not state or state.get("type") != "auto_route_confirm":
            await query.answer("Sessiya tugagan.", show_alert=True)
            return
        group = await get_or_create_group(session, chat.id, chat.title or "")
        driver = await get_driver_by_user(session, user.id, group.id)
        await clear_state(session, user.id)
        if driver is None:
            await query.answer("Driver topilmadi.", show_alert=True)
            return
        ticket = await create_ticket(
            session,
            context,
            department=state["department"],
            category=state["category"],
            driver=driver,
            driver_group=group,
            text=state["text"],
        )

    await query.answer("Yuborildi." if ticket else "Xatolik.")
    if ticket:
        await query.edit_message_text(f"✅ Ticket #{ticket.id} yaratildi.")
    else:
        await query.edit_message_text("⚠️ Bo'lim guruhi ulanmagan, admin bilan bog'laning.")


async def cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    async with session_scope() as session:
        await clear_state(session, query.from_user.id)
    await query.answer("Bekor qilindi.")
    await query.edit_message_text("❌ Bekor qilindi.")
