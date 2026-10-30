from __future__ import annotations
import datetime as dt

from sqlalchemy import select
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from . import config
from .core import build_menu, get_department_group, is_admin, is_staff_of, log_action
from .db import session_scope
from .models import Driver, Group, Ticket, TicketMedia, ensure_aware, utcnow

STATUS_LABELS = {
    "open": "🟡 Open",
    "working": "🔵 Ishda (In progress)",
    "done": "✅ Bajarildi (Done)",
}

# Departments can intercept "Done" before a ticket actually closes (e.g. maintenance
# asking for a cost entry, safety asking for an outcome) by registering a hook here.
# A hook returns True if it took over handling (ticket stays open until it later
# calls set_ticket_status itself), or False/None to let the normal close proceed.
_DONE_HOOKS: dict = {}


def register_done_hook(department: str, hook) -> None:
    _DONE_HOOKS[department] = hook


def ticket_keyboard(ticket: Ticket) -> InlineKeyboardMarkup | None:
    if ticket.status == "done":
        return None
    rows = []
    if ticket.status != "working":
        rows.append(("🕐 In progress", f"ticket:working:{ticket.id}"))
    rows.append(("✅ Done", f"ticket:done:{ticket.id}"))
    if not ticket.urgent:
        rows.append(("🔴 Urgent", f"ticket:urgent:{ticket.id}"))
    return build_menu(rows, columns=2)


def render_ticket_text(ticket: Ticket, driver: Driver) -> str:
    dept = config.DEPARTMENTS[ticket.department]
    lines = []
    if ticket.urgent:
        lines.append("🔴 URGENT")
    lines.append(f"{dept.emoji} Ticket #{ticket.id} — {dept.name_uz}")
    lines.append(f"Driver: {driver.full_name}")
    if ticket.category:
        lines.append(f"Turi: {ticket.category}")
    if ticket.text:
        lines.append(f"Matn: {ticket.text}")
    lines.append(f"Holat: {STATUS_LABELS.get(ticket.status, ticket.status)}")
    return "\n".join(lines)


async def _update_ticket_card(context, ticket: Ticket, driver: Driver, dept_group: Group) -> None:
    if not ticket.dept_message_id:
        return
    body = render_ticket_text(ticket, driver)
    keyboard = ticket_keyboard(ticket)
    try:
        await context.bot.edit_message_caption(
            chat_id=dept_group.chat_id, message_id=ticket.dept_message_id, caption=body, reply_markup=keyboard
        )
    except Exception:
        try:
            await context.bot.edit_message_text(
                chat_id=dept_group.chat_id, message_id=ticket.dept_message_id, text=body, reply_markup=keyboard
            )
        except Exception:
            pass


async def create_ticket(
    session,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    department: str,
    category: str,
    driver: Driver,
    driver_group: Group,
    text: str,
    media: list[tuple[str, str]] | None = None,
    truck_id: int | None = None,
) -> Ticket | None:
    """Create a ticket, post it to the department group, and apply cross-department forwarding.

    Returns None (and notifies admins) if the department has no bound group yet.
    """
    dept_group = await get_department_group(session, department)
    if dept_group is None:
        await context.bot.send_message(
            chat_id=driver_group.chat_id,
            text=(
                f"⚠️ {config.DEPARTMENTS[department].name_uz} guruhi hali ulanmagan. "
                "Admin bilan bog'laning."
            ),
        )
        return None

    ticket = Ticket(
        department=department,
        category=category,
        driver_id=driver.id,
        driver_group_id=driver_group.id,
        dept_group_id=dept_group.id,
        truck_id=truck_id,
        text=text,
        status="open",
    )
    session.add(ticket)
    await session.flush()

    for file_id, kind in media or []:
        session.add(TicketMedia(ticket_id=ticket.id, file_id=file_id, kind=kind))

    await _post_ticket_card(session, context, ticket, driver, dept_group)
    await _forward_cross_department(session, context, ticket, driver, department, category, media or [])
    return ticket


async def _post_ticket_card(session, context, ticket: Ticket, driver: Driver, dept_group: Group) -> None:
    media_rows = (
        await session.execute(select(TicketMedia).where(TicketMedia.ticket_id == ticket.id))
    ).scalars().all()
    body = render_ticket_text(ticket, driver)
    keyboard = ticket_keyboard(ticket)

    if media_rows:
        sent = await context.bot.send_photo(
            chat_id=dept_group.chat_id,
            photo=media_rows[0].file_id,
            caption=body,
            reply_markup=keyboard,
        )
        for extra in media_rows[1:]:
            await context.bot.send_photo(chat_id=dept_group.chat_id, photo=extra.file_id)
    else:
        sent = await context.bot.send_message(chat_id=dept_group.chat_id, text=body, reply_markup=keyboard)

    ticket.dept_message_id = sent.message_id


async def _forward_cross_department(
    session, context, ticket: Ticket, driver: Driver, department: str, category: str, media
) -> None:
    targets = config.CATEGORY_FORWARD_RULES.get((department, category), ())
    for target_code in targets:
        target_group = await get_department_group(session, target_code)
        if target_group is None:
            continue
        note = (
            f"ℹ️ {config.DEPARTMENTS[department].name_uz} dan xabar: Ticket #{ticket.id}\n"
            f"Driver: {driver.full_name} | Turi: {category}\n{ticket.text}"
        )
        if media:
            await context.bot.send_photo(chat_id=target_group.chat_id, photo=media[0][0], caption=note)
        else:
            await context.bot.send_message(chat_id=target_group.chat_id, text=note)


async def set_ticket_status(
    session, context: ContextTypes.DEFAULT_TYPE, ticket_id: int, new_status: str, actor_name: str
) -> Ticket | None:
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        return None
    ticket.status = new_status
    ticket.closed_by = actor_name if new_status == "done" else ticket.closed_by
    if new_status == "done":
        ticket.closed_at = utcnow()

    driver = await session.get(Driver, ticket.driver_id)
    dept_group = await session.get(Group, ticket.dept_group_id)
    driver_group = await session.get(Group, ticket.driver_group_id)

    await _update_ticket_card(context, ticket, driver, dept_group)

    if new_status == "done":
        await context.bot.send_message(
            chat_id=driver_group.chat_id,
            text=f"✅ Ticket #{ticket.id} ({ticket.category or ticket.department}) yopildi.",
        )
    return ticket


async def mark_urgent(
    session, context: ContextTypes.DEFAULT_TYPE, ticket_id: int, actor_name: str
) -> Ticket | None:
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None or ticket.urgent:
        return ticket
    ticket.urgent = True

    driver = await session.get(Driver, ticket.driver_id)
    dept_group = await session.get(Group, ticket.dept_group_id)
    await _update_ticket_card(context, ticket, driver, dept_group)

    if ticket.department != "dispatch":
        dispatch_group = await get_department_group(session, "dispatch")
        if dispatch_group is not None:
            await context.bot.send_message(
                chat_id=dispatch_group.chat_id,
                text=(
                    f"🔴 URGENT: Ticket #{ticket.id} ({config.DEPARTMENTS[ticket.department].name_uz}) "
                    f"— {driver.full_name}\n{ticket.text}"
                ),
            )
    return ticket


async def find_open_ticket(session, department: str, driver_id: int, category: str | None = None) -> Ticket | None:
    stmt = select(Ticket).where(
        Ticket.department == department,
        Ticket.driver_id == driver_id,
        Ticket.status != "done",
    )
    if category is not None:
        stmt = stmt.where(Ticket.category == category)
    result = await session.execute(stmt)
    return result.scalars().first()


async def list_open_tickets(session, department: str) -> list[Ticket]:
    result = await session.execute(
        select(Ticket).where(Ticket.department == department, Ticket.status != "done").order_by(Ticket.id)
    )
    return list(result.scalars().all())


async def due_for_reminder(session) -> list[Ticket]:
    result = await session.execute(select(Ticket).where(Ticket.status != "done"))
    due = []
    now = utcnow()
    for ticket in result.scalars().all():
        minutes = config.URGENT_REMINDER_MINUTES if ticket.urgent else config.DEPARTMENTS[ticket.department].reminder_minutes
        baseline = ensure_aware(ticket.last_reminder_at or ticket.created_at)
        if now - baseline >= dt.timedelta(minutes=minutes):
            due.append(ticket)
    return due


async def send_reminder(session, context: ContextTypes.DEFAULT_TYPE, ticket: Ticket) -> None:
    dept_group = await session.get(Group, ticket.dept_group_id)
    if dept_group is None:
        return
    reply_to = ticket.dept_message_id
    prefix = "🔴 URGENT " if ticket.urgent else ""
    text = f"{prefix}⏰ Eslatma: Ticket #{ticket.id} hali ochiq ({STATUS_LABELS.get(ticket.status)})."
    try:
        await context.bot.send_message(chat_id=dept_group.chat_id, text=text, reply_to_message_id=reply_to)
    except Exception:
        await context.bot.send_message(chat_id=dept_group.chat_id, text=text)
    ticket.last_reminder_at = utcnow()


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    async with session_scope() as session:
        group = await session.execute(select(Group).where(Group.chat_id == update.effective_chat.id))
        group = group.scalar_one_or_none()
        if group is None or group.kind not in config.DEPARTMENTS:
            await msg.reply_text("Bu buyruq faqat bo'lim guruhida ishlaydi.")
            return
        department = group.kind

        ticket = None
        if msg.reply_to_message:
            result = await session.execute(
                select(Ticket).where(
                    Ticket.dept_group_id == group.id,
                    Ticket.dept_message_id == msg.reply_to_message.message_id,
                )
            )
            ticket = result.scalar_one_or_none()
        if ticket is None and context.args:
            arg = " ".join(context.args)
            if arg.isdigit():
                candidate = await session.get(Ticket, int(arg))
                if candidate and candidate.department == department:
                    ticket = candidate
            else:
                result = await session.execute(
                    select(Ticket)
                    .join(Driver, Ticket.driver_id == Driver.id)
                    .where(Ticket.department == department, Ticket.status != "done")
                    .order_by(Ticket.id.desc())
                )
                for candidate in result.scalars().all():
                    driver = await session.get(Driver, candidate.driver_id)
                    if arg.lower() in driver.full_name.lower():
                        ticket = candidate
                        break

        if ticket is None:
            await msg.reply_text("Ticket topilmadi. /done <id> yoki ticket xabariga reply bilan ishlatilsin.")
            return

        await set_ticket_status(session, context, ticket.id, "done", update.effective_user.full_name)
    await msg.reply_text(f"✅ Ticket #{ticket.id} yopildi.")


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        group = await session.execute(select(Group).where(Group.chat_id == update.effective_chat.id))
        group = group.scalar_one_or_none()
        if group is None or group.kind not in config.DEPARTMENTS:
            await update.effective_message.reply_text("Bu buyruq faqat bo'lim guruhida ishlaydi.")
            return
        tickets = await list_open_tickets(session, group.kind)
        if not tickets:
            await update.effective_message.reply_text("Ochiq ticketlar yo'q.")
            return
        lines = []
        for t in tickets:
            driver = await session.get(Driver, t.driver_id)
            lines.append(f"#{t.id} {driver.full_name} — {t.category or ''} [{STATUS_LABELS[t.status]}]")
    await update.effective_message.reply_text("\n".join(lines))


async def handle_ticket_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, session) -> None:
    query = update.callback_query
    _, action, ticket_id_str = query.data.split(":")
    ticket_id = int(ticket_id_str)

    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        await query.answer("Ticket topilmadi.", show_alert=True)
        return

    user_id = query.from_user.id
    if not (is_admin(user_id) or await is_staff_of(session, user_id, ticket.department)):
        await query.answer(
            f"⛔ Faqat {config.DEPARTMENTS[ticket.department].name_uz} xodimlari uchun.", show_alert=True
        )
        return

    if action == "urgent":
        await mark_urgent(session, context, ticket_id, query.from_user.full_name)
        await log_action(session, user_id, query.from_user.full_name, "ticket_urgent", f"#{ticket_id}")
        await query.answer("🔴 Urgent belgilandi.")
        return

    if action == "done":
        hook = _DONE_HOOKS.get(ticket.department)
        if hook is not None and await hook(update, context, session, ticket):
            return

    ticket = await set_ticket_status(session, context, ticket_id, action, query.from_user.full_name)
    if ticket is None:
        await query.answer("Ticket topilmadi.", show_alert=True)
        return
    if action == "done":
        await log_action(session, user_id, query.from_user.full_name, "ticket_done", f"#{ticket_id}")
    await query.answer("Holat yangilandi." if action == "working" else "Yopildi.")
