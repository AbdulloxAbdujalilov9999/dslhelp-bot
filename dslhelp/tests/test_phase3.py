from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from dslhelp import jobs
from dslhelp.db import session_scope
from dslhelp.main import cb_ticket, generic_photo, generic_text
from dslhelp.models import CostEntry, Document, Driver, Load, Receipt, SafetyRecord, Ticket, Truck, utcnow
from dslhelp.modules import accounting, dispatch, hr, intake, maintenance, safety

from fakes import FakeChat, FakeMessage, FakeUser, make_callback_update, make_command_update
from test_phase1 import ADMIN, bind, driver_id_of, register, staffreg


# --------------------------------------------------------------- dispatch --

async def test_appointment_reminders_and_late_alert(ctx):
    await bind(ctx, 100, "Dispatch", "dispatch")
    driver = FakeUser(41, "Bekzod", "Yusupov")
    await register(ctx, 700, "Driver Group", driver)

    async with session_scope() as session:
        d = (await session.execute(select(Driver).where(Driver.telegram_user_id == 41))).scalar_one()
        load = Load(
            driver_id=d.id, driver_group_id=d.group_id, status="assigned",
            pu_address="Tashkent depot", pu_at=utcnow() + dt.timedelta(minutes=90),
        )
        session.add(load)
        await session.flush()
        load_id = load.id

    await jobs.appointment_reminder_job(ctx)
    assert any("2 soat qoldi" in (m.text or "") for m in ctx.bot.messages_to(700))

    async with session_scope() as session:
        load = await session.get(Load, load_id)
        assert load.pu_reminder_2h is True
        assert load.pu_reminder_30m is False
        load.pu_at = utcnow() + dt.timedelta(minutes=20)

    await jobs.appointment_reminder_job(ctx)
    assert any("30 daqiqadan keyin" in (m.text or "") for m in ctx.bot.messages_to(700))

    async with session_scope() as session:
        load = await session.get(Load, load_id)
        assert load.pu_reminder_30m is True
        load.pu_at = utcnow() - dt.timedelta(minutes=20)

    await jobs.appointment_reminder_job(ctx)
    assert any("LATE" in (m.text or "") for m in ctx.bot.messages_to(100))

    async with session_scope() as session:
        load = await session.get(Load, load_id)
        assert load.pu_late_alert is True


async def test_share_location_notifies_dispatch(ctx):
    await bind(ctx, 100, "Dispatch", "dispatch")
    driver = FakeUser(42, "Komil", "Narzullayev")
    await register(ctx, 700, "Driver Group", driver)
    chat = FakeChat(700, "Driver Group")

    prompt_update = make_callback_update(chat, driver, "dispatch:share_location")
    await dispatch.cb_share_location(prompt_update, ctx)

    loc_update = make_command_update(chat, driver, None, location=(41.311081, 69.279737))
    await dispatch.handle_location(loc_update, ctx)

    msgs = ctx.bot.messages_to(100)
    assert any("lokatsiyasi" in (m.text or "") and "41.311081" in (m.text or "") for m in msgs)

    async with session_scope() as session:
        d = (await session.execute(select(Driver).where(Driver.telegram_user_id == 42))).scalar_one()
        assert d.last_lat == 41.311081
        assert d.last_lon == 69.279737
        assert d.last_location_at is not None


# --------------------------------------------------------------- maintenance/trucks --

async def test_truck_commands_and_pm_reminder(ctx):
    await bind(ctx, 200, "Maintenance", "maintenance")
    maint_chat = FakeChat(200, "Maintenance")
    driver = FakeUser(43, "Sardor", "Qosimov")
    await register(ctx, 700, "Driver Group", driver)

    ctx.args = ["TRK-1"]
    first = make_command_update(maint_chat, ADMIN, "/truck")
    await maintenance.cmd_truck(first, ctx)
    assert "Yangi truck qo'shildi" in first.effective_message.replies[-1].text

    ctx.args = ["TRK-1"]
    second = make_command_update(maint_chat, ADMIN, "/truck")
    await maintenance.cmd_truck(second, ctx)
    assert "Truck TRK-1" in second.effective_message.replies[-1].text

    assign_update = make_command_update(maint_chat, ADMIN, "/assigntruck TRK-1 | Sardor Qosimov")
    await maintenance.cmd_assigntruck(assign_update, ctx)
    assert "tayinlandi" in assign_update.effective_message.replies[-1].text

    ctx.args = ["TRK-1", "95000"]
    mileage_update = make_command_update(maint_chat, ADMIN, "/mileage")
    await maintenance.cmd_mileage(mileage_update, ctx)

    async with session_scope() as session:
        truck = (await session.execute(select(Truck).where(Truck.number == "TRK-1"))).scalar_one()
        assert truck.mileage == 95000
        truck_id = truck.id

    await jobs.pm_reminder_job(ctx)
    pm_msgs = [m for m in ctx.bot.messages_to(200) if "PM muddati keldi" in (m.text or "")]
    assert len(pm_msgs) == 1

    await jobs.pm_reminder_job(ctx)
    pm_msgs_again = [m for m in ctx.bot.messages_to(200) if "PM muddati keldi" in (m.text or "")]
    assert len(pm_msgs_again) == 1  # no duplicate once the flag is set

    ctx.args = ["TRK-1"]
    pmdone_update = make_command_update(maint_chat, ADMIN, "/pmdone")
    await maintenance.cmd_pmdone(pmdone_update, ctx)

    async with session_scope() as session:
        truck = await session.get(Truck, truck_id)
        assert truck.last_pm_mileage == 95000
        assert truck.pm_alert_sent is False

    await jobs.pm_reminder_job(ctx)
    pm_msgs_final = [m for m in ctx.bot.messages_to(200) if "PM muddati keldi" in (m.text or "")]
    assert len(pm_msgs_final) == 1  # still just the one from before /pmdone


async def test_maintenance_cost_on_close(ctx):
    await bind(ctx, 200, "Maintenance", "maintenance")
    driver = FakeUser(44, "Jahongir", "Ne'matov")
    await register(ctx, 700, "Driver Group", driver)
    chat = FakeChat(700, "Driver Group")

    ctx.args = ["brake issue"]
    await maintenance.cmd_t(make_command_update(chat, driver, "/t"), ctx)
    async with session_scope() as session:
        ticket = (await session.execute(select(Ticket).where(Ticket.department == "maintenance"))).scalar_one()

    staff = FakeUser(90, "Mechanic")
    maint_chat = FakeChat(200, "Maintenance")
    await staffreg(ctx, 200, "Maintenance", staff)
    done_update = make_callback_update(maint_chat, staff, f"ticket:done:{ticket.id}")
    await cb_ticket(done_update, ctx)

    async with session_scope() as session:
        unchanged = await session.get(Ticket, ticket.id)
        assert unchanged.status != "done"  # intercepted, not closed yet

    cost_text_update = make_command_update(maint_chat, staff, "Moy almashtirish | 450000")
    await generic_text(cost_text_update, ctx)
    assert "Xarajat qayd etildi" in cost_text_update.effective_message.replies[-1].text

    async with session_scope() as session:
        closed = await session.get(Ticket, ticket.id)
        assert closed.status == "done"
        cost = (await session.execute(select(CostEntry).where(CostEntry.ticket_id == ticket.id))).scalar_one()
        assert cost.work_type == "Moy almashtirish"
        assert cost.amount == 450000.0

    costs_update = make_command_update(maint_chat, staff, "/costs")
    await maintenance.cmd_costs(costs_update, ctx)
    assert "450,000" in costs_update.effective_message.replies[-1].text


# --------------------------------------------------------------- safety --

async def test_safety_outcome_on_close(ctx):
    await bind(ctx, 100, "Dispatch", "dispatch")
    await bind(ctx, 400, "Safety", "safety")
    driver = FakeUser(45, "Bahodir", "Eshmatov")
    await register(ctx, 700, "Driver Group", driver)
    chat = FakeChat(700, "Driver Group")

    await intake.cb_choose_category(make_callback_update(chat, driver, "intake:cat:safety:0"), ctx)  # Incident
    await generic_text(make_command_update(chat, driver, "Yengil to'qnashuv"), ctx)
    await intake.cb_submit(make_callback_update(chat, driver, "intake:submit"), ctx)

    async with session_scope() as session:
        ticket = (await session.execute(select(Ticket).where(Ticket.department == "safety"))).scalar_one()

    staff = FakeUser(91, "SafetyOfficer")
    safety_chat = FakeChat(400, "Safety")
    await staffreg(ctx, 400, "Safety", staff)
    done_update = make_callback_update(safety_chat, staff, f"ticket:done:{ticket.id}")
    await cb_ticket(done_update, ctx)

    outcome_update = make_command_update(safety_chat, staff, "Yengil shikast, ta'mirlandi")
    await generic_text(outcome_update, ctx)
    assert "Natija qayd etildi" in outcome_update.effective_message.replies[-1].text

    async with session_scope() as session:
        closed = await session.get(Ticket, ticket.id)
        assert closed.status == "done"
        record = (await session.execute(select(SafetyRecord).where(SafetyRecord.ticket_id == ticket.id))).scalar_one()
        assert "ta'mirlandi" in record.outcome_text


async def test_csa_and_safetystats(ctx):
    await bind(ctx, 400, "Safety", "safety")
    safety_chat = FakeChat(400, "Safety")

    ctx.args = ["92.5", "yaxshi"]
    csa_update = make_command_update(safety_chat, ADMIN, "/csa")
    await safety.cmd_csa(csa_update, ctx)
    assert "92.5" in csa_update.effective_message.replies[-1].text

    driver = FakeUser(46, "Oybek", "Sodiqov")
    await register(ctx, 700, "Driver Group", driver)
    chat = FakeChat(700, "Driver Group")

    await intake.cb_choose_category(make_callback_update(chat, driver, "intake:cat:safety:0"), ctx)  # Incident
    await generic_text(make_command_update(chat, driver, "voqea"), ctx)
    await intake.cb_submit(make_callback_update(chat, driver, "intake:submit"), ctx)

    await intake.cb_choose_category(make_callback_update(chat, driver, "intake:cat:safety:1"), ctx)  # Inspection
    await generic_text(make_command_update(chat, driver, "tekshiruv"), ctx)
    await intake.cb_submit(make_callback_update(chat, driver, "intake:submit"), ctx)

    stats_update = make_command_update(safety_chat, ADMIN, "/safetystats")
    await safety.cmd_safetystats(stats_update, ctx)
    text = stats_update.effective_message.replies[-1].text
    assert "Incident: 1" in text
    assert "Inspection: 1" in text
    assert "92.5" in text


# --------------------------------------------------------------- hr onboarding --

async def test_hr_onboarding_full_cycle(ctx):
    await bind(ctx, 600, "HR", "hr")
    driver = FakeUser(47, "Olim", "Tursunov")
    chat = FakeChat(700, "Driver Group")

    await register(ctx, 700, "Driver Group", driver)
    assert any("CDL rasmini yuboring" in (m.text or "") for m in ctx.bot.messages_to(700))

    photo_update = make_command_update(chat, driver, None, photo="CDL_PHOTO")
    await generic_photo(photo_update, ctx)
    assert "muddati" in photo_update.effective_message.replies[-1].text

    await generic_text(make_command_update(chat, driver, "2027-05-01"), ctx)
    assert any("MEDICAL rasmini yuboring" in (m.text or "") for m in ctx.bot.messages_to(700))

    hr_chat = FakeChat(600, "HR")
    cdl_card = next(m for m in ctx.bot.messages_to(600) if m.photo == "CDL_PHOTO")
    approve_data = cdl_card.reply_markup.inline_keyboard[0][0].callback_data
    assert approve_data.startswith("onboard:approve:")

    hr_staff = FakeUser(95, "HRStaff")
    await staffreg(ctx, 600, "HR", hr_staff)
    approve_update = make_callback_update(hr_chat, hr_staff, approve_data)
    await hr.cb_onboard_decide(approve_update, ctx)
    assert any("CDL tasdiqlandi" in (m.text or "") for m in ctx.bot.messages_to(700))

    await generic_photo(make_command_update(chat, driver, None, photo="MEDICAL_PHOTO"), ctx)
    medical_date_update = make_command_update(chat, driver, "2027-06-01")
    await generic_text(medical_date_update, ctx)
    assert "Barcha hujjatlar yuborildi" in medical_date_update.effective_message.replies[-1].text

    async with session_scope() as session:
        d = (await session.execute(select(Driver).where(Driver.telegram_user_id == 47))).scalar_one()
        docs = (await session.execute(select(Document).where(Document.driver_id == d.id))).scalars().all()
        by_type = {doc.doc_type: doc for doc in docs}
        assert by_type["CDL"].status == "approved"
        assert by_type["MEDICAL"].status == "pending"


async def test_hr_onboarding_reject_retriggers(ctx):
    await bind(ctx, 600, "HR", "hr")
    driver = FakeUser(48, "Rustam", "Ergashev")
    chat = FakeChat(700, "Driver Group")
    await register(ctx, 700, "Driver Group", driver)

    await generic_photo(make_command_update(chat, driver, None, photo="CDL_PHOTO2"), ctx)
    await generic_text(make_command_update(chat, driver, "2027-05-01"), ctx)

    hr_chat = FakeChat(600, "HR")
    cdl_card = next(m for m in ctx.bot.messages_to(600) if m.photo == "CDL_PHOTO2")
    reject_data = cdl_card.reply_markup.inline_keyboard[0][1].callback_data
    assert reject_data.startswith("onboard:reject:")

    hr_staff = FakeUser(96, "HRStaff2")
    await staffreg(ctx, 600, "HR", hr_staff)
    reject_update = make_callback_update(hr_chat, hr_staff, reject_data)
    await hr.cb_onboard_decide(reject_update, ctx)

    cdl_prompts = [m for m in ctx.bot.messages_to(700) if "CDL rasmini yuboring" in (m.text or "")]
    assert len(cdl_prompts) == 2  # initial registration prompt + re-prompt after reject


# --------------------------------------------------------------- accounting --

async def test_settlement_forward(ctx):
    await bind(ctx, 500, "Accounting", "accounting")
    driver = FakeUser(49, "Shoh", "Rizayev")
    await register(ctx, 700, "Driver Group", driver)

    staff = FakeUser(97, "AccStaff")
    acc_chat = FakeChat(500, "Accounting")
    await staffreg(ctx, 500, "Accounting", staff)
    doc_msg = FakeMessage(acc_chat, staff, document="SETTLEMENT_FILE")

    ctx.args = ["Shoh", "Rizayev"]
    settlement_update = make_command_update(acc_chat, staff, "/settlement", reply_to=doc_msg)
    await accounting.cmd_settlement(settlement_update, ctx)

    assert any(
        m.document == "SETTLEMENT_FILE" and "hisob-kitobingiz" in (m.caption or "")
        for m in ctx.bot.messages_to(700)
    )
    assert "yuborildi" in settlement_update.effective_message.replies[-1].text


async def test_receipt_flow_and_summary(ctx):
    await bind(ctx, 500, "Accounting", "accounting")
    driver = FakeUser(50, "Nurlan", "Xolmatov")
    await register(ctx, 700, "Driver Group", driver)
    chat = FakeChat(700, "Driver Group")

    await intake.cb_choose_category(make_callback_update(chat, driver, "intake:cat:accounting:1"), ctx)  # Receipt
    await accounting.cb_receipt_kind(make_callback_update(chat, driver, "receipt:kind:fuel"), ctx)
    await generic_text(make_command_update(chat, driver, "150000"), ctx)
    await generic_photo(make_command_update(chat, driver, None, photo="FUEL_RECEIPT"), ctx)

    assert any(
        m.photo == "FUEL_RECEIPT" and "FUEL" in (m.caption or "")
        for m in ctx.bot.messages_to(500)
    )

    async with session_scope() as session:
        d = (await session.execute(select(Driver).where(Driver.telegram_user_id == 50))).scalar_one()
        receipt = (await session.execute(select(Receipt).where(Receipt.driver_id == d.id))).scalar_one()
        assert receipt.kind == "fuel"
        assert receipt.amount == 150000.0

    acc_chat = FakeChat(500, "Accounting")
    receipts_update = make_command_update(acc_chat, ADMIN, "/receipts")
    await accounting.cmd_receipts(receipts_update, ctx)
    text = receipts_update.effective_message.replies[-1].text
    assert "fuel" in text
    assert "150,000" in text
