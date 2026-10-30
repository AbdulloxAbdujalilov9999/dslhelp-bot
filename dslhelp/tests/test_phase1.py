from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from dslhelp import tickets
from dslhelp.db import session_scope
from dslhelp.main import cb_ticket, generic_photo, generic_text
from dslhelp.models import Driver, Group, Load, PTIRequestDriver, Ticket, utcnow
from dslhelp.modules import admin, dispatch, hr, intake, maintenance

from fakes import FakeChat, FakeUser, make_callback_update, make_command_update

ADMIN = FakeUser(999, "Admin")


async def bind(ctx, chat_id, title, code):
    update = make_command_update(FakeChat(chat_id, title), ADMIN, "/setgroup")
    ctx.args = [code]
    await admin.cmd_setgroup(update, ctx)


async def register(ctx, chat_id, title, user):
    update = make_command_update(FakeChat(chat_id, title), user, "/register")
    ctx.args = [user.first_name, user.last_name]
    await admin.cmd_register(update, ctx)


async def staffreg(ctx, chat_id, title, user):
    """Self-registers `user` as department staff of whichever department chat_id is bound to."""
    update = make_command_update(FakeChat(chat_id, title), user, "/staffreg")
    ctx.args = [user.first_name, user.last_name]
    await admin.cmd_staffreg(update, ctx)


async def driver_id_of(telegram_user_id: int) -> int:
    async with session_scope() as session:
        result = await session.execute(select(Driver).where(Driver.telegram_user_id == telegram_user_id))
        return result.scalar_one().id


# ------------------------------------------------------------- core rules --

async def test_setgroup_binds_department(ctx):
    await bind(ctx, 100, "Dispatch Group", "dispatch")
    async with session_scope() as session:
        group = (await session.execute(select(Group).where(Group.chat_id == 100))).scalar_one()
    assert group.kind == "dispatch"


async def test_setgroup_rejects_non_admin(ctx):
    non_admin = FakeUser(1, "Random")
    update = make_command_update(FakeChat(100, "Dispatch"), non_admin, "/setgroup")
    await admin.cmd_setgroup(update, ctx)
    assert "Faqat admin" in update.effective_message.replies[-1].text


async def test_register_creates_driver_and_menu_shows_departments(ctx):
    driver = FakeUser(1, "Ali", "Valiyev")
    await register(ctx, 700, "Driver Group", driver)

    menu_update = make_command_update(FakeChat(700, "Driver Group"), driver, "/menu")
    await admin.cmd_menu(menu_update, ctx)
    reply = menu_update.effective_message.replies[-1]
    assert "Bo'limni tanlang" in reply.text
    assert len(reply.reply_markup.inline_keyboard) == 4  # 6 departments + Til, 2 columns


# ------------------------------------------------------------- intake + cross-department forwarding --

async def test_eld_malfunction_forwards_to_dispatch_and_safety(ctx):
    await bind(ctx, 100, "Dispatch", "dispatch")
    await bind(ctx, 300, "ELD", "eld")
    await bind(ctx, 400, "Safety", "safety")
    driver = FakeUser(2, "Nodir", "Qosimov")
    await register(ctx, 700, "Driver Group", driver)

    chat = FakeChat(700, "Driver Group")
    open_update = make_callback_update(chat, driver, "dept:open:eld")
    await admin.cb_open_department(open_update, ctx)

    cat_update = make_callback_update(chat, driver, "intake:cat:eld:0")
    await intake.cb_choose_category(cat_update, ctx)

    text_update = make_command_update(chat, driver, "ELD sensor ishlamayapti")
    await generic_text(text_update, ctx)

    submit_update = make_callback_update(chat, driver, "intake:submit")
    await intake.cb_submit(submit_update, ctx)

    assert "Yuborildi" in submit_update.callback_query.message.text

    eld_msgs = ctx.bot.messages_to(300)
    assert any("ELD malfunction" in (m.text or "") for m in eld_msgs)

    dispatch_msgs = ctx.bot.messages_to(100)
    safety_msgs = ctx.bot.messages_to(400)
    assert any("ELD malfunction" in (m.text or "") for m in dispatch_msgs)
    assert any("ELD malfunction" in (m.text or "") for m in safety_msgs)

    async with session_scope() as session:
        ticket = (await session.execute(select(Ticket).where(Ticket.department == "eld"))).scalar_one()
    assert ticket.category == "ELD malfunction"
    assert ticket.status == "open"


async def test_safety_incident_forwards_to_dispatch_only(ctx):
    await bind(ctx, 100, "Dispatch", "dispatch")
    await bind(ctx, 400, "Safety", "safety")
    driver = FakeUser(3, "Olim", "Rashidov")
    await register(ctx, 700, "Driver Group", driver)
    chat = FakeChat(700, "Driver Group")

    await intake.show_category_menu(make_command_update(chat, driver, "/menu"), ctx, "safety")
    cat_update = make_callback_update(chat, driver, "intake:cat:safety:0")  # "Incident"
    await intake.cb_choose_category(cat_update, ctx)
    await generic_text(make_command_update(chat, driver, "Yo'lda avariya bo'ldi"), ctx)
    await intake.cb_submit(make_callback_update(chat, driver, "intake:submit"), ctx)

    assert any("Incident" in (m.text or "") for m in ctx.bot.messages_to(100))
    assert any("Incident" in (m.text or "") for m in ctx.bot.messages_to(400))


# ------------------------------------------------------------- maintenance /t duplicate guard --

async def test_maintenance_t_blocks_second_open_request(ctx):
    await bind(ctx, 200, "Maintenance", "maintenance")
    driver = FakeUser(4, "Sardor", "Botirov")
    await register(ctx, 700, "Driver Group", driver)
    chat = FakeChat(700, "Driver Group")

    first = make_command_update(chat, driver, "/t")
    ctx.args = ["motor", "shovqin"]
    await maintenance.cmd_t(first, ctx)
    assert "✅" in first.effective_message.replies[-1].text

    second = make_command_update(chat, driver, "/t")
    ctx.args = ["yana", "bir", "narsa"]
    await maintenance.cmd_t(second, ctx)
    assert "allaqachon so'ragansiz" in second.effective_message.replies[-1].text

    async with session_scope() as session:
        count = len((await session.execute(select(Ticket).where(Ticket.department == "maintenance"))).scalars().all())
    assert count == 1


# ------------------------------------------------------------- ticket status buttons --

async def test_ticket_done_notifies_driver_group(ctx):
    # Uses ELD (not Maintenance) deliberately: Maintenance's "Done" button is
    # intercepted by a cost-entry hook in Phase 3, so it no longer closes immediately.
    await bind(ctx, 300, "ELD", "eld")
    driver = FakeUser(5, "Jasur", "Tohirov")
    await register(ctx, 700, "Driver Group", driver)
    chat = FakeChat(700, "Driver Group")

    await intake.cb_choose_category(make_callback_update(chat, driver, "intake:cat:eld:1"), ctx)  # "Log edit"
    await generic_text(make_command_update(chat, driver, "Log tuzatish kerak"), ctx)
    await intake.cb_submit(make_callback_update(chat, driver, "intake:submit"), ctx)
    async with session_scope() as session:
        ticket = (await session.execute(select(Ticket).where(Ticket.department == "eld"))).scalar_one()

    staff = FakeUser(60, "Staff")
    eld_chat = FakeChat(300, "ELD")
    await staffreg(ctx, 300, "ELD", staff)

    working_update = make_callback_update(eld_chat, staff, f"ticket:working:{ticket.id}")
    await cb_ticket(working_update, ctx)

    done_update = make_callback_update(eld_chat, staff, f"ticket:done:{ticket.id}")
    await cb_ticket(done_update, ctx)

    assert any(f"#{ticket.id}" in (m.text or "") and "yopildi" in (m.text or "") for m in ctx.bot.messages_to(700))

    async with session_scope() as session:
        refreshed = await session.get(Ticket, ticket.id)
    assert refreshed.status == "done"
    assert refreshed.closed_at is not None


# ------------------------------------------------------------- dispatch BOL/POD --

async def test_bol_then_pod_notifies_accounting_with_bol(ctx):
    await bind(ctx, 100, "Dispatch", "dispatch")
    await bind(ctx, 500, "Accounting", "accounting")
    driver = FakeUser(6, "Davron", "Yusupov")
    await register(ctx, 700, "Driver Group", driver)
    chat = FakeChat(700, "Driver Group")

    bol_update = make_command_update(chat, driver, None, photo="FILE_BOL_1", caption="/BOL")
    await dispatch.cmd_bol(bol_update, ctx)
    assert "qabul qilindi" in bol_update.effective_message.replies[-1].text

    pod_update = make_command_update(chat, driver, "/pod")
    ctx.args = ["TRL-123"]
    await dispatch.cmd_pod(pod_update, ctx)
    assert "Accounting" in pod_update.effective_message.replies[-1].text

    accounting_msgs = ctx.bot.messages_to(500)
    assert any(m.photo == "FILE_BOL_1" for m in accounting_msgs)

    async with session_scope() as session:
        load = (await session.execute(select(Load).where(Load.driver_id.in_(
            select(Driver.id).where(Driver.telegram_user_id == 6)
        )))).scalars().first()
    assert load.status == "pod"
    assert load.pod_trailer == "TRL-123"


async def test_pod_bobtail(ctx):
    await bind(ctx, 100, "Dispatch", "dispatch")
    driver = FakeUser(7, "Botir", "Nazarov")
    await register(ctx, 700, "Driver Group", driver)
    chat = FakeChat(700, "Driver Group")

    pod_update = make_command_update(chat, driver, "/pod")
    ctx.args = ["BOBTAIL"]
    await dispatch.cmd_pod(pod_update, ctx)

    async with session_scope() as session:
        driver_row = (await session.execute(select(Driver).where(Driver.telegram_user_id == 7))).scalar_one()
        load = (await session.execute(select(Load).where(Load.driver_id == driver_row.id))).scalar_one()
    assert load.pod_trailer == "BOBTAIL"


# ------------------------------------------------------------- PTI flow --

async def test_pti_full_cycle_approve(ctx):
    await bind(ctx, 200, "Maintenance", "maintenance")
    driver = FakeUser(10, "Vali", "Aliyev")
    await register(ctx, 700, "Driver Group A", driver)
    driver_db_id = await driver_id_of(10)

    staff = FakeUser(60, "Staff")
    maint_chat = FakeChat(200, "Maintenance")

    await maintenance.cb_pti_new(make_callback_update(maint_chat, staff, "maintenance:pti_new"), ctx)
    await maintenance.cb_pti_toggle(
        make_callback_update(maint_chat, staff, f"pti:toggle:{driver_db_id}"), ctx
    )
    await maintenance.cb_pti_send(make_callback_update(maint_chat, staff, "pti:send"), ctx)

    assert any("PTI rasmlarini yuboring" in (m.text or "") for m in ctx.bot.messages_to(700))

    async with session_scope() as session:
        rd = (await session.execute(select(PTIRequestDriver).where(PTIRequestDriver.driver_id == driver_db_id))).scalar_one()
    assert rd.status == "pending"

    driver_chat = FakeChat(700, "Driver Group A")
    await generic_photo(make_command_update(driver_chat, driver, None, photo="PTI_PHOTO_1"), ctx)

    done_update = make_callback_update(driver_chat, driver, f"pti:done:{rd.id}")
    await maintenance.cb_pti_done(done_update, ctx)

    assert any(m.photo == "PTI_PHOTO_1" for m in ctx.bot.messages_to(200))

    approve_update = make_callback_update(maint_chat, staff, f"pti:approve:{rd.id}")
    await maintenance.cb_pti_approve_redo(approve_update, ctx)

    assert any("tasdiqlandi" in (m.text or "") for m in ctx.bot.messages_to(700))

    async with session_scope() as session:
        refreshed = await session.get(PTIRequestDriver, rd.id)
    assert refreshed.status == "approved"

    status_update = make_callback_update(maint_chat, staff, "maintenance:pti_status")
    await maintenance.cb_pti_status(status_update, ctx)
    status_text = ctx.bot.messages_to(200)[-1].text
    assert "approved" in status_text


async def test_pti_redo_resets_photos(ctx):
    await bind(ctx, 200, "Maintenance", "maintenance")
    driver = FakeUser(11, "Sanjar", "Eshonov")
    await register(ctx, 701, "Driver Group B", driver)
    driver_db_id = await driver_id_of(11)

    staff = FakeUser(60, "Staff")
    maint_chat = FakeChat(200, "Maintenance")

    await maintenance.cb_pti_new(make_callback_update(maint_chat, staff, "maintenance:pti_new"), ctx)
    await maintenance.cb_pti_toggle(make_callback_update(maint_chat, staff, f"pti:toggle:{driver_db_id}"), ctx)
    await maintenance.cb_pti_send(make_callback_update(maint_chat, staff, "pti:send"), ctx)

    async with session_scope() as session:
        rd = (await session.execute(select(PTIRequestDriver).where(PTIRequestDriver.driver_id == driver_db_id))).scalar_one()

    driver_chat = FakeChat(701, "Driver Group B")
    await generic_photo(make_command_update(driver_chat, driver, None, photo="BAD_PHOTO"), ctx)
    await maintenance.cb_pti_done(make_callback_update(driver_chat, driver, f"pti:done:{rd.id}"), ctx)

    await maintenance.cb_pti_approve_redo(make_callback_update(maint_chat, staff, f"pti:redo:{rd.id}"), ctx)

    assert any("qayta yuboring" in (m.text or "") for m in ctx.bot.messages_to(701))

    async with session_scope() as session:
        refreshed = await session.get(PTIRequestDriver, rd.id)
    assert refreshed.status == "redo"


# ------------------------------------------------------------- HR docs + reminders --

async def test_hr_doc_and_docs(ctx):
    await bind(ctx, 600, "HR", "hr")
    driver = FakeUser(20, "Umid", "Rahimov")
    await register(ctx, 700, "Driver Group", driver)

    hr_chat = FakeChat(600, "HR")
    future_date = (dt.date.today() + dt.timedelta(days=5)).isoformat()
    doc_update = make_command_update(hr_chat, ADMIN, "/doc")
    ctx.args = ["Umid", "Rahimov", "CDL", future_date]
    await hr.cmd_doc(doc_update, ctx)
    assert "saqlandi" in doc_update.effective_message.replies[-1].text

    docs_update = make_command_update(hr_chat, ADMIN, "/docs")
    await hr.cmd_docs(docs_update, ctx)
    assert "CDL" in docs_update.effective_message.replies[-1].text


async def test_reminder_due_after_elapsed_time(ctx):
    await bind(ctx, 200, "Maintenance", "maintenance")
    driver = FakeUser(30, "Shoxrux", "Karimov")
    await register(ctx, 700, "Driver Group", driver)
    chat = FakeChat(700, "Driver Group")

    ctx.args = ["issue"]
    await maintenance.cmd_t(make_command_update(chat, driver, "/t"), ctx)

    async with session_scope() as session:
        ticket = (await session.execute(select(Ticket).where(Ticket.department == "maintenance"))).scalar_one()
        ticket.created_at = utcnow() - dt.timedelta(minutes=10)  # maintenance reminder = 5 min
        due = await tickets.due_for_reminder(session)
        assert any(t.id == ticket.id for t in due)
        await tickets.send_reminder(session, ctx, ticket)

    assert any("Eslatma" in (m.text or "") for m in ctx.bot.messages_to(200))
