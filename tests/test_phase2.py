from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from dslhelp import jobs, tickets
from dslhelp.db import session_scope
from dslhelp.main import cb_ticket
from dslhelp.models import Driver, Load, Ticket, utcnow
from dslhelp.modules import dispatch, export, hr, maintenance, search

from fakes import FakeChat, FakeUser, make_callback_update, make_command_update
from test_phase1 import ADMIN, bind, driver_id_of, register, staffreg


async def test_newload_assigns_and_bol_reuses_card(ctx):
    await bind(ctx, 100, "Dispatch", "dispatch")
    driver = FakeUser(40, "Olmos", "Tursunov")
    await register(ctx, 700, "Driver Group", driver)
    dispatch_chat = FakeChat(100, "Dispatch")

    newload_update = make_command_update(
        dispatch_chat,
        ADMIN,
        "/newload Olmos Tursunov | LOAD-77 | ABC Logistics | Chicago, IL | 2026-11-02 08:00 | Dallas, TX | 2026-11-03 14:00",
    )
    await dispatch.cmd_newload(newload_update, ctx)
    assert "yaratildi" in newload_update.effective_message.replies[-1].text
    assert any("Yangi load tayinlandi" in (m.text or "") for m in ctx.bot.messages_to(700))

    loads_update = make_command_update(dispatch_chat, ADMIN, "/loads")
    await dispatch.cmd_loads(loads_update, ctx)
    assert "LOAD-77" in loads_update.effective_message.replies[-1].text

    driver_chat = FakeChat(700, "Driver Group")
    bol_update = make_command_update(driver_chat, driver, None, photo="BOL_FILE")
    await dispatch.cmd_bol(bol_update, ctx)

    async with session_scope() as session:
        loads = (await session.execute(select(Load).where(Load.load_number == "LOAD-77"))).scalars().all()
    assert len(loads) == 1
    assert loads[0].bol_file_id == "BOL_FILE"


async def test_urgent_ticket_pings_dispatch_and_speeds_up_reminder(ctx):
    await bind(ctx, 100, "Dispatch", "dispatch")
    await bind(ctx, 200, "Maintenance", "maintenance")
    driver = FakeUser(41, "Bekzod", "Yusupov")
    await register(ctx, 700, "Driver Group", driver)
    chat = FakeChat(700, "Driver Group")

    ctx.args = ["motor yonib ketdi"]
    await maintenance.cmd_t(make_command_update(chat, driver, "/t"), ctx)

    async with session_scope() as session:
        ticket = (await session.execute(select(Ticket).where(Ticket.department == "maintenance"))).scalar_one()

    staff = FakeUser(70, "Staff")
    maint_chat = FakeChat(200, "Maintenance")
    await staffreg(ctx, 200, "Maintenance", staff)
    urgent_update = make_callback_update(maint_chat, staff, f"ticket:urgent:{ticket.id}")
    await cb_ticket(urgent_update, ctx)

    assert any("URGENT" in (m.text or "") for m in ctx.bot.messages_to(100))

    async with session_scope() as session:
        refreshed = await session.get(Ticket, ticket.id)
        assert refreshed.urgent is True
        refreshed.created_at = utcnow() - dt.timedelta(minutes=3)
        due = await tickets.due_for_reminder(session)
        assert any(t.id == refreshed.id for t in due)


async def test_daily_report_includes_metrics(ctx):
    await bind(ctx, 100, "Dispatch", "dispatch")
    await bind(ctx, 200, "Maintenance", "maintenance")
    await bind(ctx, 600, "HR", "hr")

    driver1 = FakeUser(50, "Aziz", "Komilov")
    driver2 = FakeUser(51, "Diyor", "Sharipov")
    await register(ctx, 700, "Driver Group A", driver1)
    await register(ctx, 701, "Driver Group B", driver2)

    ctx.args = ["issue"]
    await maintenance.cmd_t(make_command_update(FakeChat(700, "Driver Group A"), driver1, "/t"), ctx)

    async with session_scope() as session:
        d1 = (await session.execute(select(Driver).where(Driver.telegram_user_id == 50))).scalar_one()
        session.add(Load(driver_id=d1.id, driver_group_id=d1.group_id, status="assigned", load_number="L1"))

    staff = FakeUser(80, "Staff")
    maint_chat = FakeChat(200, "Maintenance")
    driver2_db_id = await driver_id_of(51)
    await maintenance.cb_pti_new(make_callback_update(maint_chat, staff, "maintenance:pti_new"), ctx)
    await maintenance.cb_pti_toggle(make_callback_update(maint_chat, staff, f"pti:toggle:{driver2_db_id}"), ctx)
    await maintenance.cb_pti_send(make_callback_update(maint_chat, staff, "pti:send"), ctx)

    hr_chat = FakeChat(600, "HR")
    soon = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    ctx.args = ["Aziz", "Komilov", "CDL", soon]
    await hr.cmd_doc(make_command_update(hr_chat, ADMIN, "/doc"), ctx)

    await jobs.daily_report_job(ctx)

    reports = [m for m in ctx.bot.messages_to(100) if "Kunlik hisobot" in (m.text or "")]
    assert reports
    text = reports[-1].text
    assert "Faol loadlar: 1" in text
    assert "Maintenance: 1" in text
    assert "PTI qilmagan driverlar (bugun): 2" in text
    assert "Javob bermagan driverlar (PTI so'roviga): 1" in text
    assert "Diyor Sharipov" in text
    assert "Aziz Komilov" in text

    admin_dm = [m for m in ctx.bot.messages_to(999) if "Kunlik hisobot" in (m.text or "")]
    assert admin_dm


async def test_driver_profile_and_find(ctx):
    await bind(ctx, 200, "Maintenance", "maintenance")
    driver = FakeUser(90, "Elyor", "Nabiyev")
    await register(ctx, 700, "Driver Group", driver)
    chat = FakeChat(700, "Driver Group")

    ctx.args = ["brake issue"]
    await maintenance.cmd_t(make_command_update(chat, driver, "/t"), ctx)

    maint_chat = FakeChat(200, "Maintenance")
    driver_update = make_command_update(maint_chat, ADMIN, "/driver")
    ctx.args = ["Elyor", "Nabiyev"]
    await search.cmd_driver(driver_update, ctx)
    reply = driver_update.effective_message.replies[-1].text
    assert "Elyor Nabiyev" in reply
    assert "Ochiq ticketlar" in reply

    find_update = make_command_update(maint_chat, ADMIN, "/find")
    ctx.args = ["Elyor"]
    await search.cmd_find(find_update, ctx)
    find_reply = find_update.effective_message.replies[-1]
    assert find_reply.reply_markup is not None
    assert len(find_reply.reply_markup.inline_keyboard) == 1

    btn_data = find_reply.reply_markup.inline_keyboard[0][0].callback_data
    profile_update = make_callback_update(maint_chat, ADMIN, btn_data)
    await search.cb_driver_profile(profile_update, ctx)
    assert "Elyor Nabiyev" in profile_update.callback_query.message.replies[-1].text


async def test_export_sends_xlsx_document(ctx):
    await bind(ctx, 100, "Dispatch", "dispatch")
    driver = FakeUser(95, "Farrux", "Ismoilov")
    await register(ctx, 700, "Driver Group", driver)
    chat = FakeChat(700, "Driver Group")

    bol_update = make_command_update(chat, driver, None, photo="BOLX")
    await dispatch.cmd_bol(bol_update, ctx)

    dispatch_chat = FakeChat(100, "Dispatch")
    export_update = make_command_update(dispatch_chat, ADMIN, "/export")
    await export.cmd_export(export_update, ctx)
    assert export_update.effective_message.replies[-1].reply_markup is not None

    period_update = make_callback_update(dispatch_chat, ADMIN, "export:period:today")
    await export.cb_export_period(period_update, ctx)

    docs = [m for m in ctx.bot.sent if m.document is not None]
    assert docs
    assert docs[-1].filename == "dslhelp_export_today.xlsx"
    assert len(docs[-1].document) > 0
