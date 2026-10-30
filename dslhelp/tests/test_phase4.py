from __future__ import annotations

import json

from sqlalchemy import select

from dslhelp import config as dconfig
from dslhelp import jobs
from dslhelp.db import session_scope
from dslhelp.main import cb_ticket, generic_text
from dslhelp.models import Ticket
from dslhelp.modules import admin, dispatch, intake

from fakes import FakeChat, FakeUser, make_callback_update, make_command_update
from test_phase1 import ADMIN, bind, register, staffreg


async def test_staffreg_gates_newload(ctx):
    await bind(ctx, 100, "Dispatch", "dispatch")
    driver = FakeUser(60, "Olim", "Qosimov")
    await register(ctx, 700, "Driver Group", driver)
    dispatch_chat = FakeChat(100, "Dispatch")
    random_person = FakeUser(61, "Random", "Person")

    denied = make_command_update(dispatch_chat, random_person, "/newload Olim Qosimov | L1 | X | A | | B |")
    await dispatch.cmd_newload(denied, ctx)
    assert "xodimlari uchun" in denied.effective_message.replies[-1].text

    await staffreg(ctx, 100, "Dispatch", random_person)
    allowed = make_command_update(dispatch_chat, random_person, "/newload Olim Qosimov | L1 | X | A | | B |")
    await dispatch.cmd_newload(allowed, ctx)
    assert "yaratildi" in allowed.effective_message.replies[-1].text


async def test_ticket_button_requires_staff_or_admin(ctx):
    await bind(ctx, 300, "ELD", "eld")
    driver = FakeUser(62, "Baxtiyor", "Yusupov")
    await register(ctx, 700, "Driver Group", driver)
    chat = FakeChat(700, "Driver Group")

    await intake.cb_choose_category(make_callback_update(chat, driver, "intake:cat:eld:1"), ctx)  # Log edit
    await generic_text(make_command_update(chat, driver, "muammo bor"), ctx)
    await intake.cb_submit(make_callback_update(chat, driver, "intake:submit"), ctx)

    async with session_scope() as session:
        ticket = (await session.execute(select(Ticket).where(Ticket.department == "eld"))).scalar_one()

    random_person = FakeUser(63, "Random2", "Person")
    eld_chat = FakeChat(300, "ELD")

    denied_update = make_callback_update(eld_chat, random_person, f"ticket:working:{ticket.id}")
    await cb_ticket(denied_update, ctx)
    assert any(a["show_alert"] for a in denied_update.callback_query.answers)

    async with session_scope() as session:
        unchanged = await session.get(Ticket, ticket.id)
        assert unchanged.status == "open"

    await staffreg(ctx, 300, "ELD", random_person)
    allowed_update = make_callback_update(eld_chat, random_person, f"ticket:working:{ticket.id}")
    await cb_ticket(allowed_update, ctx)

    async with session_scope() as session:
        changed = await session.get(Ticket, ticket.id)
        assert changed.status == "working"


async def test_auditlog_records_setgroup(ctx):
    await bind(ctx, 100, "Dispatch", "dispatch")
    audit_update = make_command_update(FakeChat(100, "Dispatch"), ADMIN, "/auditlog")
    await admin.cmd_auditlog(audit_update, ctx)
    text = audit_update.effective_message.replies[-1].text
    assert "setgroup" in text
    assert "Admin" in text


async def test_language_selection_changes_menu_text(ctx):
    driver = FakeUser(64, "Shahzod", "Rustamov")
    await register(ctx, 700, "Driver Group", driver)
    chat = FakeChat(700, "Driver Group")

    set_lang_update = make_callback_update(chat, driver, "lang:set:en")
    await admin.cb_set_language(set_lang_update, ctx)

    menu_update = make_command_update(chat, driver, "/menu")
    await admin.cmd_menu(menu_update, ctx)
    assert "Choose a department" in menu_update.effective_message.replies[-1].text


async def test_backup_job_writes_json(ctx, tmp_path):
    original_dir = dconfig.BACKUP_DIR
    dconfig.BACKUP_DIR = str(tmp_path)
    try:
        await bind(ctx, 100, "Dispatch", "dispatch")
        await jobs.backup_job(ctx)
        files = list(tmp_path.glob("backup-*.json"))
        assert len(files) == 1
        content = json.loads(files[0].read_text())
        assert "groups" in content
        assert len(content["groups"]) >= 1
    finally:
        dconfig.BACKUP_DIR = original_dir


async def test_web_dashboard_routes_and_token(ctx):
    from starlette.testclient import TestClient

    from dslhelp.webapp import app

    await bind(ctx, 100, "Dispatch", "dispatch")
    driver = FakeUser(65, "Davron", "Usmonov")
    await register(ctx, 700, "Driver Group", driver)

    original_token = dconfig.WEB_PANEL_TOKEN
    dconfig.WEB_PANEL_TOKEN = "secret123"
    try:
        with TestClient(app) as client:
            assert client.get("/").status_code == 403
            assert client.get("/?token=wrong").status_code == 403

            overview = client.get("/?token=secret123")
            assert overview.status_code == 200
            assert "DSL HELP" in overview.text

            drivers_page = client.get("/drivers?token=secret123")
            assert drivers_page.status_code == 200
            assert "Davron Usmonov" in drivers_page.text

            for path in ["loads", "tickets", "trucks", "audit"]:
                assert client.get(f"/{path}?token=secret123").status_code == 200
    finally:
        dconfig.WEB_PANEL_TOKEN = original_token
