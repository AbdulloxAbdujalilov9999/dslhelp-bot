from __future__ import annotations
from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from .. import config
from ..core import build_menu, get_driver_by_user, get_or_create_group, is_admin, log_action
from ..db import session_scope
from ..i18n import tr
from ..models import AuditLog, Driver, Group, Staff

LANGUAGE_ROWS = [("🇺🇿 O'zbek", "lang:set:uz"), ("🇷🇺 Русский", "lang:set:ru"), ("🇬🇧 English", "lang:set:en")]

DEPT_ALIASES = {d.name_uz.split(" ", 1)[1].lower(): code for code, d in config.DEPARTMENTS.items()}


def resolve_department_code(raw: str) -> str | None:
    key = raw.strip().lower().lstrip("/")
    if key in config.DEPARTMENTS:
        return key
    return DEPT_ALIASES.get(key)


async def cmd_setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_admin(user.id):
        await update.effective_message.reply_text("⛔ Faqat admin /setgroup buyrug'ini ishlatishi mumkin.")
        return
    if not context.args:
        names = ", ".join(d.name_uz for d in config.DEPARTMENTS.values())
        await update.effective_message.reply_text(f"Foydalanish: /setgroup <bo'lim>\nBo'limlar: {names}")
        return

    code = resolve_department_code(" ".join(context.args))
    if code is None:
        await update.effective_message.reply_text("Bo'lim nomi noto'g'ri. /setgroup ni argumentsiz yuboring.")
        return

    async with session_scope() as session:
        try:
            await _bind_department(session, context, update.effective_chat.id, update.effective_chat.title or "", code)
        except ValueError:
            await update.effective_message.reply_text(
                "⛔ Bu guruhda driverlar ro'yxatdan o'tgan, bo'lim sifatida ishlatib bo'lmaydi."
            )
            return
        await log_action(session, update.effective_user.id, update.effective_user.full_name, "setgroup", code)
    await update.effective_message.reply_text(f"✅ Bu guruh endi {config.DEPARTMENTS[code].name_uz} bo'limiga ulandi.")


async def _bind_department(session, context, chat_id: int, title: str, code: str) -> None:
    group = await get_or_create_group(session, chat_id, title)

    if group.kind == "driver":
        existing_drivers = (
            await session.execute(select(Driver).where(Driver.group_id == group.id))
        ).scalars().first()
        if existing_drivers is not None:
            raise ValueError("driver group already has registered drivers")

    # One group per department: unbind any previous holder of this code.
    prev = (await session.execute(select(Group).where(Group.kind == code))).scalar_one_or_none()
    if prev is not None and prev.id != group.id:
        prev.kind = None
        try:
            await context.bot.send_message(
                chat_id=prev.chat_id,
                text=f"ℹ️ Bu guruh {config.DEPARTMENTS[code].name_uz} bo'limidan uzildi (boshqa guruhga ko'chirildi).",
            )
        except Exception:
            pass

    group.kind = code


async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Foydalanish: /register Ism Familiya")
        return

    user = update.effective_user
    chat = update.effective_chat
    full = " ".join(context.args)
    parts = full.split(" ", 1)
    first_name, last_name = parts[0], (parts[1] if len(parts) > 1 else "")

    async with session_scope() as session:
        group = await get_or_create_group(session, chat.id, chat.title or "")
        if group.kind is not None and group.kind != "driver":
            await update.effective_message.reply_text(
                "⛔ Bu guruh bo'lim guruhi sifatida band. /register faqat driver guruhida ishlaydi."
            )
            return
        group.kind = "driver"

        driver = await get_driver_by_user(session, user.id, group.id)
        is_new = driver is None
        if driver is None:
            driver = Driver(
                telegram_user_id=user.id, first_name=first_name, last_name=last_name, group_id=group.id
            )
            session.add(driver)
            await session.flush()
            reply = tr("register_success", driver.language, name=full)
        else:
            driver.first_name, driver.last_name = first_name, last_name
            reply = tr("register_updated", driver.language, name=full)

        if is_new:
            from . import hr

            await hr.advance_onboarding(session, context, driver)

    await update.effective_message.reply_text(reply)


async def cmd_staffreg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Foydalanish: /staffreg Ism Familiya")
        return

    user = update.effective_user
    chat = update.effective_chat
    full = " ".join(context.args)
    parts = full.split(" ", 1)
    first_name, last_name = parts[0], (parts[1] if len(parts) > 1 else "")

    async with session_scope() as session:
        group = await get_or_create_group(session, chat.id, chat.title or "")
        if group.kind not in config.DEPARTMENTS:
            await update.effective_message.reply_text(
                "⛔ /staffreg faqat bo'lim guruhida ishlaydi (avval admin /setgroup bilan ulashi kerak)."
            )
            return
        department = group.kind

        result = await session.execute(
            select(Staff).where(Staff.telegram_user_id == user.id, Staff.department == department)
        )
        staff = result.scalar_one_or_none()
        if staff is None:
            staff = Staff(telegram_user_id=user.id, department=department, first_name=first_name, last_name=last_name)
            session.add(staff)
            reply = f"✅ {config.DEPARTMENTS[department].name_uz} xodimi sifatida ro'yxatdan o'tdingiz: {full}"
        else:
            staff.first_name, staff.last_name, staff.active = first_name, last_name, True
            reply = f"✅ Ma'lumotlar yangilandi: {full}"

        await log_action(session, user.id, full, "staffreg", department)

    await update.effective_message.reply_text(reply)


DEPARTMENT_MENU_ROWS = [(d.name_uz, f"dept:open:{code}") for code, d in config.DEPARTMENTS.items()]
DRIVER_MENU_ROWS = DEPARTMENT_MENU_ROWS + [("🌐 Til", "lang:menu")]
SETGROUP_MENU_ROWS = [(d.name_uz, f"setgroup:{code}") for code, d in config.DEPARTMENTS.items()]


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_menu(update, context)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_menu(update, context, greet=True)


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, greet: bool = False) -> None:
    chat = update.effective_chat
    user = update.effective_user

    async with session_scope() as session:
        group = await get_or_create_group(session, chat.id, chat.title or "")

        if group.kind in config.DEPARTMENTS:
            text, keyboard = await _department_panel(session, group)
        elif group.kind == "driver":
            driver = await get_driver_by_user(session, user.id, group.id)
            if driver is None:
                text = "👋 Xush kelibsiz! " + tr("register_prompt")
                keyboard = None
            else:
                lang = driver.language or "uz"
                hello = tr("menu_greeting", lang, name=driver.first_name) + "\n" if greet else ""
                text = hello + tr("menu_choose_department", lang)
                keyboard = build_menu(DRIVER_MENU_ROWS, columns=2)
        else:
            if is_admin(user.id):
                text = "Bu guruh hali ulanmagan. Bo'limni tanlang (shu guruhni shu bo'limga ulash uchun):"
                keyboard = build_menu(SETGROUP_MENU_ROWS, columns=2)
            else:
                text = (
                    "Bu guruh hali hech qaysi bo'limga ulanmagan.\n"
                    "Admin /setgroup bilan ulashi kerak, yoki driver guruhi bo'lsa /register Ism Familiya yozing."
                )
                keyboard = None

    await update.effective_message.reply_text(text, reply_markup=keyboard)


async def _department_panel(session, group: Group):
    from ..tickets import list_open_tickets

    code = group.kind
    dept = config.DEPARTMENTS[code]
    open_tickets = await list_open_tickets(session, code) if dept.categories or code == "maintenance" else []
    rows = [
        ("📋 Open tickets", f"panel:open:{code}"),
        ("📜 History", f"panel:history:{code}"),
        ("🧑‍✈️ Drivers", f"panel:drivers:{code}"),
    ]
    rows.extend(_extra_panel_rows(code))
    text = f"{dept.name_uz} paneli\nOchiq ticketlar: {len(open_tickets)}"
    return text, build_menu(rows, columns=2)


def _extra_panel_rows(code: str) -> list[tuple[str, str]]:
    if code == "dispatch":
        return [("🚚 Active loads", "dispatch:active_loads")]
    if code == "maintenance":
        return [("🧰 PTI so'rovi", "maintenance:pti_new"), ("📶 PTI status", "maintenance:pti_status")]
    if code == "hr":
        return [("📄 Hujjatlar (/docs)", "hr:docs")]
    return []


async def cb_setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = query.from_user
    if not is_admin(user.id):
        await query.answer("Faqat admin.", show_alert=True)
        return
    code = query.data.split(":", 1)[1]
    async with session_scope() as session:
        try:
            await _bind_department(session, context, update.effective_chat.id, update.effective_chat.title or "", code)
        except ValueError:
            await query.answer("Bu guruhda driverlar ro'yxatdan o'tgan, bo'lim sifatida ishlatib bo'lmaydi.", show_alert=True)
            return
        await log_action(session, user.id, user.full_name, "setgroup", code)
    await query.answer("Ulandi.")
    await query.edit_message_text(f"✅ Bu guruh endi {config.DEPARTMENTS[code].name_uz} bo'limiga ulandi.")


async def cb_open_department(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    code = query.data.split(":", 2)[2]
    await query.answer()
    if code == "dispatch":
        from . import dispatch as dispatch_mod

        await dispatch_mod.show_driver_menu(update, context)
    elif code == "maintenance":
        from . import maintenance as maintenance_mod

        await maintenance_mod.show_driver_menu(update, context)
    elif code in config.INTAKE_DEPARTMENTS:
        from . import intake

        await intake.show_category_menu(update, context, code)


async def cb_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from ..tickets import STATUS_LABELS, list_open_tickets

    query = update.callback_query
    _, action, code = query.data.split(":")
    await query.answer()

    async with session_scope() as session:
        if action == "open":
            tickets = await list_open_tickets(session, code)
            if not tickets:
                text = "Ochiq ticketlar yo'q."
            else:
                lines = []
                for t in tickets:
                    driver = await session.get(Driver, t.driver_id)
                    lines.append(f"#{t.id} {driver.full_name} — {t.category or ''} [{STATUS_LABELS[t.status]}]")
                text = "\n".join(lines)
        elif action == "history":
            text = await _history_text(session, code)
        elif action == "drivers":
            result = await session.execute(select(Driver).where(Driver.active.is_(True)).order_by(Driver.id))
            drivers = result.scalars().all()
            text = "\n".join(f"{d.full_name} (guruh: {d.group_id})" for d in drivers) or "Driverlar yo'q."
        else:
            text = "Noma'lum amal."

    await context.bot.send_message(chat_id=update.effective_chat.id, text=text)


async def _history_text(session, code: str) -> str:
    from sqlalchemy import select as sa_select

    from ..models import Ticket

    result = await session.execute(
        sa_select(Ticket).where(Ticket.department == code, Ticket.status == "done").order_by(Ticket.id.desc()).limit(10)
    )
    tickets = result.scalars().all()
    if not tickets:
        return "Tarix bo'sh."
    lines = []
    for t in tickets:
        driver = await session.get(Driver, t.driver_id)
        lines.append(f"#{t.id} {driver.full_name} — {t.category or ''} ✅ ({t.closed_by})")
    return "\n".join(lines)


async def cmd_auditlog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ /auditlog faqat admin uchun.")
        return

    limit = 20
    if context.args:
        try:
            limit = max(1, min(100, int(context.args[0])))
        except ValueError:
            pass

    async with session_scope() as session:
        result = await session.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))
        entries = result.scalars().all()

    if not entries:
        await update.effective_message.reply_text("Audit log bo'sh.")
        return

    lines = [f"📜 Audit log (oxirgi {len(entries)}):"]
    for e in entries:
        when = e.created_at.strftime("%Y-%m-%d %H:%M")
        detail = f" — {e.detail}" if e.detail else ""
        lines.append(f"[{when}] {e.actor_name}: {e.action}{detail}")

    await update.effective_message.reply_text("\n".join(lines))


# --------------------------------------------------------------- language --

async def cmd_til(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(tr("choose_language"), reply_markup=build_menu(LANGUAGE_ROWS, columns=1))


async def cb_lang_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(tr("choose_language"), reply_markup=build_menu(LANGUAGE_ROWS, columns=1))


async def cb_set_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    lang = query.data.split(":")[2]

    async with session_scope() as session:
        group = await get_or_create_group(session, update.effective_chat.id, update.effective_chat.title or "")
        driver = await get_driver_by_user(session, query.from_user.id, group.id)
        if driver is None:
            await query.answer("Avval ro'yxatdan o'ting: /register Ism Familiya", show_alert=True)
            return
        driver.language = lang

    await query.answer()
    await query.message.reply_text(tr("language_set", lang))
