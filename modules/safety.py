from __future__ import annotations
from zoneinfo import ZoneInfo

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from .. import config
from ..core import clear_state, get_or_create_group, local_today, log_action, require_staff_or_admin, set_state
from ..db import session_scope
from ..models import CSAScore, SafetyRecord, Ticket, ensure_aware
from ..tickets import register_done_hook, set_ticket_status

OUTCOME_HINT = "Natijani kiriting (masalan: 'Yengil shikast, ta'mirlandi' yoki 'Tekshiruv o'tdi')."


async def _safety_done_hook(update: Update, context: ContextTypes.DEFAULT_TYPE, session, ticket: Ticket) -> bool:
    query = update.callback_query
    await set_state(session, query.from_user.id, {"type": "safety_close", "ticket_id": ticket.id})
    await query.answer()
    await query.message.reply_text(f"🛡 Ishni yopishdan oldin: {OUTCOME_HINT}")
    return True


register_done_hook("safety", _safety_done_hook)


async def handle_close_outcome_text(update: Update, context: ContextTypes.DEFAULT_TYPE, state: dict) -> None:
    outcome_text = update.effective_message.text or ""
    ticket_id = state["ticket_id"]

    async with session_scope() as session:
        ticket = await session.get(Ticket, ticket_id)
        if ticket is None:
            await clear_state(session, update.effective_user.id)
            return
        session.add(SafetyRecord(ticket_id=ticket.id, category=ticket.category, outcome_text=outcome_text))
        await clear_state(session, update.effective_user.id)
        await set_ticket_status(session, context, ticket_id, "done", update.effective_user.full_name)

    await update.effective_message.reply_text("✅ Natija qayd etildi. Ticket yopildi.")


async def cmd_csa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Foydalanish: /csa <ball> [izoh]")
        return
    try:
        score = float(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Ball raqam bo'lishi kerak.")
        return
    note = " ".join(context.args[1:])

    async with session_scope() as session:
        group = await get_or_create_group(session, update.effective_chat.id, update.effective_chat.title or "")
        if group.kind != "safety":
            await update.effective_message.reply_text("Bu buyruq faqat Safety guruhida ishlaydi.")
            return
        if not await require_staff_or_admin(update, session, "safety"):
            return
        session.add(CSAScore(score=score, note=note))
        await log_action(session, update.effective_user.id, update.effective_user.full_name, "csa", f"{score}")

    await update.effective_message.reply_text(f"✅ CSA ball qayd etildi: {score}" + (f" ({note})" if note else ""))


async def cmd_safetystats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    month_start = local_today().replace(day=1)

    async with session_scope() as session:
        tickets = (await session.execute(select(Ticket).where(Ticket.department == "safety"))).scalars().all()
        this_month = [
            t for t in tickets
            if ensure_aware(t.created_at).astimezone(ZoneInfo(config.TIMEZONE)).date() >= month_start
        ]
        incident_count = sum(1 for t in this_month if t.category == "Incident")
        inspection_count = sum(1 for t in this_month if t.category == "Inspection")

        scores = (await session.execute(select(CSAScore).order_by(CSAScore.id.desc()).limit(5))).scalars().all()

    lines = [
        f"🛡 Safety statistikasi ({month_start.isoformat()} dan):",
        f"Incident: {incident_count}",
        f"Inspection: {inspection_count}",
    ]
    if scores:
        lines.append("Oxirgi CSA ballar:")
        for s in scores:
            lines.append(f"  {s.score}" + (f" — {s.note}" if s.note else ""))
    else:
        lines.append("CSA ball yozilmagan.")

    await update.effective_message.reply_text("\n".join(lines))
