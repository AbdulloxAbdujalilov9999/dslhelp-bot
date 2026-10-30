from __future__ import annotations
import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from . import config
from .models import AuditLog, ConversationState, Driver, Group, Staff, Truck, ensure_aware, utcnow


def build_menu(rows: list[tuple[str, str]], columns: int = 2) -> InlineKeyboardMarkup:
    """Stack (text, callback_data) pairs into a grid, `columns` wide, last row may be shorter."""
    buttons = [InlineKeyboardButton(text, callback_data=data) for text, data in rows]
    grid = [buttons[i : i + columns] for i in range(0, len(buttons), columns)]
    return InlineKeyboardMarkup(grid)


async def get_or_create_group(session, chat_id: int, title: str = "") -> Group:
    result = await session.execute(select(Group).where(Group.chat_id == chat_id))
    group = result.scalar_one_or_none()
    if group is None:
        group = Group(chat_id=chat_id, title=title, kind=None)
        session.add(group)
        await session.flush()
    elif title and group.title != title:
        group.title = title
    return group


async def get_group(session, chat_id: int) -> Group | None:
    result = await session.execute(select(Group).where(Group.chat_id == chat_id))
    return result.scalar_one_or_none()


async def get_driver_by_user(session, telegram_user_id: int, group_id: int) -> Driver | None:
    result = await session.execute(
        select(Driver).where(Driver.telegram_user_id == telegram_user_id, Driver.group_id == group_id)
    )
    return result.scalar_one_or_none()


async def get_driver_any_group(session, telegram_user_id: int) -> Driver | None:
    result = await session.execute(
        select(Driver).where(Driver.telegram_user_id == telegram_user_id).order_by(Driver.id.desc())
    )
    return result.scalars().first()


def local_today() -> dt.date:
    """Today's calendar date in config.TIMEZONE, not the host process's OS timezone.

    dt.date.today() depends on wherever the bot happens to be deployed (often UTC);
    using it to cut off "today" against config.TIMEZONE-local business hours drifts
    near midnight. Anchor every such cutoff to the configured business timezone instead.
    """
    return utcnow().astimezone(ZoneInfo(config.TIMEZONE)).date()


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


async def get_department_group(session, department: str) -> Group | None:
    result = await session.execute(select(Group).where(Group.kind == department))
    return result.scalar_one_or_none()


async def get_driver_truck(session, driver_id: int) -> Truck | None:
    result = await session.execute(select(Truck).where(Truck.driver_id == driver_id))
    return result.scalar_one_or_none()


# ---- roles: admin (config.ADMIN_IDS) / department staff (self-registered) / driver ----

async def is_staff_of(session, telegram_user_id: int, department: str) -> bool:
    result = await session.execute(
        select(Staff).where(
            Staff.telegram_user_id == telegram_user_id,
            Staff.department == department,
            Staff.active.is_(True),
        )
    )
    return result.scalar_one_or_none() is not None


async def require_staff_or_admin(update: Update, session, department: str) -> bool:
    """Replies with an error and returns False unless the sender is staff of
    `department` or an admin. Use at the top of department-staff-only commands."""
    user_id = update.effective_user.id
    if is_admin(user_id) or await is_staff_of(session, user_id, department):
        return True
    await update.effective_message.reply_text(
        f"⛔ Bu buyruq faqat {config.DEPARTMENTS[department].name_uz} xodimlari uchun. "
        f"Avval ro'yxatdan o'ting: /staffreg Ism Familiya"
    )
    return False


async def log_action(session, actor_user_id: int, actor_name: str, action: str, detail: str = "") -> None:
    session.add(AuditLog(actor_user_id=actor_user_id, actor_name=actor_name, action=action, detail=detail))


# ---- per-user conversation state (for intake / PTI submission flows) ----

async def get_state(session, telegram_user_id: int) -> dict | None:
    result = await session.execute(
        select(ConversationState).where(ConversationState.telegram_user_id == telegram_user_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    age = utcnow() - ensure_aware(row.updated_at)
    if age > dt.timedelta(minutes=config.STATE_TTL_MINUTES):
        await session.delete(row)
        return None
    return row.data


async def set_state(session, telegram_user_id: int, data: dict) -> None:
    result = await session.execute(
        select(ConversationState).where(ConversationState.telegram_user_id == telegram_user_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = ConversationState(telegram_user_id=telegram_user_id, data=data, updated_at=utcnow())
        session.add(row)
    else:
        row.data = data
        row.updated_at = utcnow()


async def clear_state(session, telegram_user_id: int) -> None:
    result = await session.execute(
        select(ConversationState).where(ConversationState.telegram_user_id == telegram_user_id)
    )
    row = result.scalar_one_or_none()
    if row is not None:
        await session.delete(row)


def chat_id_of(update: Update) -> int:
    return update.effective_chat.id


def user_id_of(update: Update) -> int:
    return update.effective_user.id


async def notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text)
        except Exception:
            pass
