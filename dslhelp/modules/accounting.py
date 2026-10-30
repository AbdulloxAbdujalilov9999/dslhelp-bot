from __future__ import annotations
from zoneinfo import ZoneInfo

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from .. import config
from ..core import (
    build_menu,
    clear_state,
    get_department_group,
    get_driver_by_user,
    get_or_create_group,
    local_today,
    log_action,
    require_staff_or_admin,
    set_state,
)
from ..db import session_scope
from ..models import Driver, Group, Receipt, ensure_aware

RECEIPT_KINDS = [("⛽ Fuel", "fuel"), ("⚖️ Scale", "scale"), ("📦 Lumper", "lumper")]


# --------------------------------------------------------------- settlement --

async def cmd_settlement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    reply_to = msg.reply_to_message
    if not reply_to or not (reply_to.document or reply_to.photo):
        await msg.reply_text(
            "Foydalanish: hisob-kitob faylga/rasmga reply qilib /settlement <driver ism> yozing."
        )
        return
    if not context.args:
        await msg.reply_text("Driver ismini kiriting: /settlement <ism>")
        return
    name = " ".join(context.args)

    async with session_scope() as session:
        group = await get_or_create_group(session, update.effective_chat.id, update.effective_chat.title or "")
        if group.kind != "accounting":
            await msg.reply_text("Bu buyruq faqat Accounting guruhida ishlaydi.")
            return
        if not await require_staff_or_admin(update, session, "accounting"):
            return

        drivers = (await session.execute(select(Driver).where(Driver.active.is_(True)))).scalars().all()
        matches = [d for d in drivers if d.full_name.lower() == name.lower()]
        if not matches:
            matches = [d for d in drivers if name.lower() in d.full_name.lower()]
        if not matches:
            await msg.reply_text(f"Driver topilmadi: {name}")
            return
        if len(matches) > 1:
            names = "\n".join(f"- {d.full_name}" for d in matches)
            await msg.reply_text(f"Bir nechta driver topildi:\n{names}")
            return

        driver = matches[0]
        driver_group = await session.get(Group, driver.group_id)
        driver_name = driver.full_name
        await log_action(session, update.effective_user.id, update.effective_user.full_name, "settlement", driver_name)

    caption = f"💰 {driver_name}, haftalik hisob-kitobingiz."
    if reply_to.document:
        await context.bot.send_document(chat_id=driver_group.chat_id, document=reply_to.document.file_id, caption=caption)
    else:
        await context.bot.send_photo(chat_id=driver_group.chat_id, photo=reply_to.photo[-1].file_id, caption=caption)
    await msg.reply_text(f"✅ {driver_name}ga yuborildi.")


# ------------------------------------------------------------- receipt flow --

async def start_receipt_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, driver: Driver) -> None:
    rows = [(label, f"receipt:kind:{kind}") for label, kind in RECEIPT_KINDS]
    await update.effective_message.reply_text("Chek turini tanlang:", reply_markup=build_menu(rows, columns=1))


async def cb_receipt_kind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    kind = query.data.split(":")[2]
    user = update.effective_user
    chat = update.effective_chat

    async with session_scope() as session:
        group = await get_or_create_group(session, chat.id, chat.title or "")
        driver = await get_driver_by_user(session, user.id, group.id)
        if driver is None:
            await query.answer("Avval ro'yxatdan o'ting: /register Ism Familiya", show_alert=True)
            return
        await set_state(session, user.id, {"type": "receipt", "kind": kind, "amount": None})

    await query.answer()
    label = next(label for label, k in RECEIPT_KINDS if k == kind)
    await query.message.reply_text(f"{label}: summani kiriting (raqam):")


async def handle_receipt_amount_text(update: Update, context: ContextTypes.DEFAULT_TYPE, state: dict) -> None:
    text = (update.effective_message.text or "").strip()
    try:
        amount = float(text.replace(",", ""))
    except ValueError:
        await update.effective_message.reply_text("Summani raqam sifatida kiriting.")
        return
    state["amount"] = amount
    async with session_scope() as session:
        await set_state(session, update.effective_user.id, state)
    await update.effective_message.reply_text("📷 Chek rasmini yuboring:")


async def handle_receipt_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, state: dict) -> None:
    file_id = update.effective_message.photo[-1].file_id
    user = update.effective_user
    chat = update.effective_chat

    async with session_scope() as session:
        group = await get_or_create_group(session, chat.id, chat.title or "")
        driver = await get_driver_by_user(session, user.id, group.id)
        if driver is None:
            await clear_state(session, user.id)
            return
        amount = state.get("amount") or 0.0
        kind = state["kind"]
        session.add(Receipt(driver_id=driver.id, kind=kind, amount=amount, file_id=file_id))
        await clear_state(session, user.id)
        driver_name = driver.full_name

        accounting_group = await get_department_group(session, "accounting")
        if accounting_group is not None:
            await context.bot.send_photo(
                chat_id=accounting_group.chat_id,
                photo=file_id,
                caption=f"🧾 {driver_name}: {kind.upper()} chek — {amount:,.0f}",
            )

    await update.effective_message.reply_text("✅ Chek qabul qilindi.")


# ---------------------------------------------------------------- /receipts --

async def cmd_receipts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        group = await get_or_create_group(session, update.effective_chat.id, update.effective_chat.title or "")
        if group.kind != "accounting":
            await update.effective_message.reply_text("Bu buyruq faqat Accounting guruhida ishlaydi.")
            return

        month_start = local_today().replace(day=1)
        receipts = (await session.execute(select(Receipt))).scalars().all()
        this_month = [
            r for r in receipts
            if ensure_aware(r.created_at).astimezone(ZoneInfo(config.TIMEZONE)).date() >= month_start
        ]
        if not this_month:
            await update.effective_message.reply_text("Bu oyda chek yo'q.")
            return

        by_kind: dict[str, float] = {}
        total = 0.0
        for r in this_month:
            by_kind[r.kind] = by_kind.get(r.kind, 0.0) + r.amount
            total += r.amount

        lines = [f"🧾 Cheklar ({month_start.isoformat()} dan):"]
        for kind, amount in by_kind.items():
            lines.append(f"  {kind}: {amount:,.0f}")
        lines.append(f"Jami: {total:,.0f}")

    await update.effective_message.reply_text("\n".join(lines))
