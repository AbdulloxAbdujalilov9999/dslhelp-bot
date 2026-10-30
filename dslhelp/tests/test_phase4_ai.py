from __future__ import annotations

from sqlalchemy import select

from dslhelp import ai
from dslhelp import config as dconfig
from dslhelp.core import clear_state
from dslhelp.db import session_scope
from dslhelp.main import generic_text
from dslhelp.models import Load, Ticket
from dslhelp.modules import autoroute, dispatch

from fakes import FakeChat, FakeUser, make_callback_update, make_command_update
from test_phase1 import bind, register


class FakeContentBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeAnthropicResponse:
    def __init__(self, text):
        self.content = [FakeContentBlock(text)]


class FakeMessages:
    def __init__(self, response_text):
        self._response_text = response_text

    async def create(self, **kwargs):
        return FakeAnthropicResponse(self._response_text)


class FakeAnthropicClient:
    def __init__(self, response_text):
        self.messages = FakeMessages(response_text)


# ------------------------------------------------------------- ai.py unit tests --

async def test_classify_department_parses_mocked_response(monkeypatch):
    fake_client = FakeAnthropicClient('{"department": "hr", "category": "Vacation"}')
    monkeypatch.setattr(ai, "_get_client", lambda: fake_client)
    result = await ai.classify_department("Men ta'tilga chiqmoqchiman")
    assert result == {"department": "hr", "category": "Vacation"}


async def test_classify_department_rejects_out_of_menu_option(monkeypatch):
    fake_client = FakeAnthropicClient('{"department": "dispatch", "category": "Nonexistent"}')
    monkeypatch.setattr(ai, "_get_client", lambda: fake_client)
    result = await ai.classify_department("random text")
    assert result is None


async def test_classify_department_handles_null_route(monkeypatch):
    fake_client = FakeAnthropicClient('{"department": null, "category": null}')
    monkeypatch.setattr(ai, "_get_client", lambda: fake_client)
    result = await ai.classify_department("hech narsaga aloqasi yo'q matn")
    assert result is None


async def test_extract_bol_fields_strips_markdown_fence(monkeypatch):
    fenced = '```json\n{"load_number": "L1", "address": "NY", "amount": 500}\n```'
    fake_client = FakeAnthropicClient(fenced)
    monkeypatch.setattr(ai, "_get_client", lambda: fake_client)
    result = await ai.extract_bol_fields(b"fake-bytes")
    assert result == {"load_number": "L1", "address": "NY", "amount": 500}


async def test_extract_bol_fields_returns_none_on_bad_json(monkeypatch):
    fake_client = FakeAnthropicClient("not json at all")
    monkeypatch.setattr(ai, "_get_client", lambda: fake_client)
    result = await ai.extract_bol_fields(b"fake-bytes")
    assert result is None


# ------------------------------------------------------------- BOL OCR flow --

async def test_bol_ocr_confirm_writes_to_load(ctx, monkeypatch):
    monkeypatch.setattr(dconfig, "ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(dispatch, "_download_photo_bytes", _fake_download)
    monkeypatch.setattr(ai, "extract_bol_fields", _fake_extract_ok)

    await bind(ctx, 100, "Dispatch", "dispatch")
    driver = FakeUser(70, "Davron", "Nazarov")
    await register(ctx, 700, "Driver Group", driver)
    chat = FakeChat(700, "Driver Group")

    bol_update = make_command_update(chat, driver, None, photo="BOL_IMG")
    await dispatch.cmd_bol(bol_update, ctx)

    ocr_msgs = [m for m in ctx.bot.sent + bol_update.effective_message.replies if "LOAD-999" in (m.text or "")]
    assert ocr_msgs
    confirm_data = ocr_msgs[-1].reply_markup.inline_keyboard[0][0].callback_data
    assert confirm_data == "ocr:bol:confirm"

    confirm_update = make_callback_update(chat, driver, confirm_data)
    await dispatch.cb_ocr_bol_confirm(confirm_update, ctx)

    async with session_scope() as session:
        load = (await session.execute(select(Load).where(Load.load_number == "LOAD-999"))).scalar_one()
        assert load.pu_address == "Chicago, IL"


async def test_bol_ocr_skip_leaves_load_untouched(ctx, monkeypatch):
    monkeypatch.setattr(dconfig, "ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(dispatch, "_download_photo_bytes", _fake_download)
    monkeypatch.setattr(ai, "extract_bol_fields", _fake_extract_ok)

    await bind(ctx, 100, "Dispatch", "dispatch")
    driver = FakeUser(71, "Elbek", "Qodirov")
    await register(ctx, 701, "Driver Group B", driver)
    chat = FakeChat(701, "Driver Group B")

    bol_update = make_command_update(chat, driver, None, photo="BOL_IMG2")
    await dispatch.cmd_bol(bol_update, ctx)

    ocr_msgs = [m for m in ctx.bot.sent + bol_update.effective_message.replies if "LOAD-999" in (m.text or "")]
    skip_data = ocr_msgs[-1].reply_markup.inline_keyboard[0][1].callback_data
    assert skip_data == "ocr:bol:skip"

    skip_update = make_callback_update(chat, driver, skip_data)
    await dispatch.cb_ocr_bol_skip(skip_update, ctx)

    async with session_scope() as session:
        loads = (await session.execute(select(Load).where(Load.load_number == "LOAD-999"))).scalars().all()
        assert not loads


async def test_bol_ocr_disabled_without_api_key(ctx, monkeypatch):
    monkeypatch.setattr(dispatch, "_download_photo_bytes", _fake_download)
    monkeypatch.setattr(ai, "extract_bol_fields", _fake_extract_ok)
    assert not dconfig.ANTHROPIC_API_KEY  # default: unset in the test environment

    await bind(ctx, 100, "Dispatch", "dispatch")
    driver = FakeUser(72, "Jasur", "Sharipov")
    await register(ctx, 700, "Driver Group", driver)
    chat = FakeChat(700, "Driver Group")

    bol_update = make_command_update(chat, driver, None, photo="BOL_IMG3")
    await dispatch.cmd_bol(bol_update, ctx)

    assert not any("LOAD-999" in (m.text or "") for m in ctx.bot.sent + bol_update.effective_message.replies)


async def _fake_download(context, file_id):
    return b"fake-bytes"


async def _fake_extract_ok(image_bytes, media_type="image/jpeg"):
    return {"load_number": "LOAD-999", "address": "Chicago, IL", "amount": 1200.0}


# ------------------------------------------------------------- auto-routing flow --

async def test_auto_route_confirm_creates_ticket(ctx, monkeypatch):
    monkeypatch.setattr(dconfig, "ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(ai, "classify_department", _fake_classify_eld)

    await bind(ctx, 300, "ELD", "eld")
    driver = FakeUser(73, "Aziz", "Normatov")
    await register(ctx, 700, "Driver Group", driver)
    async with session_scope() as session:
        await clear_state(session, driver.id)  # driver isn't mid-onboarding for this test
    chat = FakeChat(700, "Driver Group")

    text_update = make_command_update(chat, driver, "ELD qurilmasi ishlamayapti, nima qilsam bo'ladi")
    await generic_text(text_update, ctx)

    reply = text_update.effective_message.replies[-1]
    assert "ELD" in reply.text
    confirm_data = reply.reply_markup.inline_keyboard[0][0].callback_data
    assert confirm_data == "autoroute:confirm"

    confirm_update = make_callback_update(chat, driver, confirm_data)
    await autoroute.cb_confirm(confirm_update, ctx)

    async with session_scope() as session:
        ticket = (await session.execute(select(Ticket).where(Ticket.department == "eld"))).scalar_one()
        assert ticket.category == "Log edit"


async def test_auto_route_cancel_creates_no_ticket(ctx, monkeypatch):
    monkeypatch.setattr(dconfig, "ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(ai, "classify_department", _fake_classify_eld)

    await bind(ctx, 300, "ELD", "eld")
    driver = FakeUser(74, "Bekzod", "Islomov")
    await register(ctx, 700, "Driver Group", driver)
    async with session_scope() as session:
        await clear_state(session, driver.id)
    chat = FakeChat(700, "Driver Group")

    text_update = make_command_update(chat, driver, "ELD qurilmasi ishlamayapti, qandoq tuzataman")
    await generic_text(text_update, ctx)
    reply = text_update.effective_message.replies[-1]
    cancel_data = reply.reply_markup.inline_keyboard[0][1].callback_data
    assert cancel_data == "autoroute:cancel"

    cancel_update = make_callback_update(chat, driver, cancel_data)
    await autoroute.cb_cancel(cancel_update, ctx)

    async with session_scope() as session:
        tickets = (await session.execute(select(Ticket).where(Ticket.department == "eld"))).scalars().all()
        assert not tickets


async def test_auto_route_no_match_sends_nothing(ctx, monkeypatch):
    monkeypatch.setattr(dconfig, "ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(ai, "classify_department", _fake_classify_none)

    driver = FakeUser(75, "Nodir", "Toshmatov")
    await register(ctx, 700, "Driver Group", driver)
    async with session_scope() as session:
        await clear_state(session, driver.id)
    chat = FakeChat(700, "Driver Group")

    text_update = make_command_update(chat, driver, "Salom qalaysiz bugun ob-havo yaxshi")
    await generic_text(text_update, ctx)

    assert not text_update.effective_message.replies


async def _fake_classify_eld(text):
    return {"department": "eld", "category": "Log edit"}


async def _fake_classify_none(text):
    return None
