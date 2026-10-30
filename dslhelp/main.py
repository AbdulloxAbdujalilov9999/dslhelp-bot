from __future__ import annotations
import datetime as dt
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from . import config
from .core import get_state, notify_admins
from .db import init_db, session_scope
from .jobs import appointment_reminder_job, backup_job, daily_report_job, pm_reminder_job, reminder_job
from .modules import accounting, admin, autoroute, dispatch, export, hr, intake, maintenance, safety, search
from . import tickets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("dslhelp")


# ----------------------------------------------------------------- caption commands --

def _args_from_caption(update: Update) -> list[str]:
    caption = update.effective_message.caption or ""
    return caption.split()[1:]


async def on_bol_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.args = _args_from_caption(update)
    await dispatch.cmd_bol(update, context)


async def on_pod_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.args = _args_from_caption(update)
    await dispatch.cmd_pod(update, context)


# ----------------------------------------------------------------- generic text/photo --

async def generic_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        state = await get_state(session, update.effective_user.id)
    if not state:
        if config.ANTHROPIC_API_KEY:
            await autoroute.try_auto_route(update, context)
        return
    if state.get("type") == "intake":
        await intake.handle_intake_text(update, context, state)
    elif state.get("type") == "maintenance_close":
        await maintenance.handle_close_cost_text(update, context, state)
    elif state.get("type") == "safety_close":
        await safety.handle_close_outcome_text(update, context, state)
    elif state.get("type") == "onboarding" and state.get("awaiting") == "date":
        await hr.handle_onboarding_date_text(update, context, state)
    elif state.get("type") == "receipt" and state.get("amount") is None:
        await accounting.handle_receipt_amount_text(update, context, state)


async def generic_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        state = await get_state(session, update.effective_user.id)
    if not state:
        return
    if state.get("type") == "intake":
        await intake.handle_intake_photo(update, context, state)
    elif state.get("type") == "pti_submit":
        await maintenance.handle_pti_photo(update, context, state)
    elif state.get("type") == "onboarding" and state.get("awaiting") == "photo":
        await hr.handle_onboarding_photo(update, context, state)
    elif state.get("type") == "receipt" and state.get("amount") is not None:
        await accounting.handle_receipt_photo(update, context, state)


async def cb_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        await tickets.handle_ticket_callback(update, context, session)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error", exc_info=context.error)
    try:
        await notify_admins(context, f"⚠️ Bot xatosi: {context.error}")
    except Exception:
        pass


# ----------------------------------------------------------------- health check server --

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, fmt, *args):  # silence default stderr logging
        pass


def start_health_server(port: int) -> None:
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Health check server listening on :%s", port)


def build_application() -> Application:
    application = Application.builder().token(config.BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", admin.cmd_start))
    application.add_handler(CommandHandler("menu", admin.cmd_menu))
    application.add_handler(CommandHandler("setgroup", admin.cmd_setgroup))
    application.add_handler(CommandHandler("register", admin.cmd_register))
    application.add_handler(CommandHandler("staffreg", admin.cmd_staffreg))
    application.add_handler(CommandHandler("auditlog", admin.cmd_auditlog))
    application.add_handler(CommandHandler("til", admin.cmd_til))

    application.add_handler(MessageHandler(filters.PHOTO & filters.CaptionRegex(r"(?i)^/bol(\s|$)"), on_bol_caption))
    application.add_handler(MessageHandler(filters.PHOTO & filters.CaptionRegex(r"(?i)^/pod(\s|$)"), on_pod_caption))
    application.add_handler(CommandHandler("bol", dispatch.cmd_bol))
    application.add_handler(CommandHandler("pod", dispatch.cmd_pod))
    application.add_handler(CommandHandler("pu", dispatch.cmd_pu))
    application.add_handler(CommandHandler("del", dispatch.cmd_del))
    application.add_handler(CommandHandler("status", dispatch.cmd_status))

    application.add_handler(CommandHandler("t", maintenance.cmd_t))
    application.add_handler(CommandHandler("truck", maintenance.cmd_truck))
    application.add_handler(CommandHandler("assigntruck", maintenance.cmd_assigntruck))
    application.add_handler(CommandHandler("mileage", maintenance.cmd_mileage))
    application.add_handler(CommandHandler("pmdone", maintenance.cmd_pmdone))
    application.add_handler(CommandHandler("costs", maintenance.cmd_costs))

    application.add_handler(CommandHandler("csa", safety.cmd_csa))
    application.add_handler(CommandHandler("safetystats", safety.cmd_safetystats))

    application.add_handler(CommandHandler("settlement", accounting.cmd_settlement))
    application.add_handler(CommandHandler("receipts", accounting.cmd_receipts))
    application.add_handler(CommandHandler("doc", hr.cmd_doc))
    application.add_handler(CommandHandler("docs", hr.cmd_docs))

    application.add_handler(CommandHandler("done", tickets.cmd_done))
    application.add_handler(CommandHandler("pending", tickets.cmd_pending))

    application.add_handler(CommandHandler("newload", dispatch.cmd_newload))
    application.add_handler(CommandHandler("loads", dispatch.cmd_loads))

    application.add_handler(CommandHandler("find", search.cmd_find))
    application.add_handler(CommandHandler("driver", search.cmd_driver))

    application.add_handler(CommandHandler("export", export.cmd_export))

    application.add_handler(CallbackQueryHandler(admin.cb_open_department, pattern=r"^dept:open:"))
    application.add_handler(CallbackQueryHandler(admin.cb_setgroup, pattern=r"^setgroup:"))
    application.add_handler(CallbackQueryHandler(admin.cb_panel, pattern=r"^panel:"))
    application.add_handler(CallbackQueryHandler(admin.cb_lang_menu, pattern=r"^lang:menu$"))
    application.add_handler(CallbackQueryHandler(admin.cb_set_language, pattern=r"^lang:set:"))
    application.add_handler(CallbackQueryHandler(cb_ticket, pattern=r"^ticket:"))

    application.add_handler(CallbackQueryHandler(dispatch.cb_active_loads, pattern=r"^dispatch:active_loads$"))
    application.add_handler(CallbackQueryHandler(dispatch.cb_hint, pattern=r"^dispatch:(bol|pod)_hint$"))
    application.add_handler(CallbackQueryHandler(dispatch.cb_share_location, pattern=r"^dispatch:share_location$"))
    application.add_handler(CallbackQueryHandler(dispatch.cb_ocr_bol_confirm, pattern=r"^ocr:bol:confirm$"))
    application.add_handler(CallbackQueryHandler(dispatch.cb_ocr_bol_skip, pattern=r"^ocr:bol:skip$"))
    application.add_handler(MessageHandler(filters.LOCATION, dispatch.handle_location))

    application.add_handler(CallbackQueryHandler(maintenance.cb_t_hint, pattern=r"^maintenance:t_hint$"))
    application.add_handler(CallbackQueryHandler(maintenance.cb_pti_new, pattern=r"^maintenance:pti_new$"))
    application.add_handler(CallbackQueryHandler(maintenance.cb_pti_status, pattern=r"^maintenance:pti_status$"))
    application.add_handler(CallbackQueryHandler(maintenance.cb_pti_toggle, pattern=r"^pti:toggle:"))
    application.add_handler(CallbackQueryHandler(maintenance.cb_pti_page, pattern=r"^pti:page:"))
    application.add_handler(CallbackQueryHandler(maintenance.cb_pti_send, pattern=r"^pti:send$"))
    application.add_handler(CallbackQueryHandler(maintenance.cb_pti_cancel, pattern=r"^pti:cancel$"))
    application.add_handler(CallbackQueryHandler(maintenance.cb_pti_done, pattern=r"^pti:done:"))
    application.add_handler(CallbackQueryHandler(maintenance.cb_pti_approve_redo, pattern=r"^pti:(approve|redo):"))

    application.add_handler(CallbackQueryHandler(hr.cb_docs, pattern=r"^hr:docs$"))
    application.add_handler(CallbackQueryHandler(hr.cb_onboard_decide, pattern=r"^onboard:"))
    application.add_handler(CallbackQueryHandler(accounting.cb_receipt_kind, pattern=r"^receipt:kind:"))

    application.add_handler(CallbackQueryHandler(intake.cb_choose_category, pattern=r"^intake:cat:"))
    application.add_handler(CallbackQueryHandler(intake.cb_submit, pattern=r"^intake:submit$"))
    application.add_handler(CallbackQueryHandler(intake.cb_cancel, pattern=r"^intake:cancel$"))

    application.add_handler(CallbackQueryHandler(search.cb_driver_profile, pattern=r"^search:driverprofile:"))
    application.add_handler(CallbackQueryHandler(search.cb_search_ticket, pattern=r"^search:ticket:"))
    application.add_handler(CallbackQueryHandler(search.cb_search_load, pattern=r"^search:load:"))

    application.add_handler(CallbackQueryHandler(export.cb_export_period, pattern=r"^export:period:"))

    application.add_handler(CallbackQueryHandler(autoroute.cb_confirm, pattern=r"^autoroute:confirm$"))
    application.add_handler(CallbackQueryHandler(autoroute.cb_cancel, pattern=r"^autoroute:cancel$"))

    # Generic fallbacks must be added last so specific commands/callbacks above take precedence.
    application.add_handler(MessageHandler(filters.PHOTO, generic_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, generic_text))

    application.add_error_handler(on_error)
    return application


async def _post_init(application: Application) -> None:
    await init_db()
    logger.info("Database ready (%s)", config.DATABASE_URL.split("://")[0])


def main() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN environment variable is required")

    application = build_application()
    application.post_init = _post_init

    application.job_queue.run_repeating(reminder_job, interval=config.REMINDER_POLL_SECONDS, first=10)
    application.job_queue.run_repeating(appointment_reminder_job, interval=config.REMINDER_POLL_SECONDS, first=15)
    application.job_queue.run_repeating(pm_reminder_job, interval=config.REMINDER_POLL_SECONDS, first=20)
    application.job_queue.run_daily(
        hr.run_daily_doc_check,
        time=dt.time(hour=config.DOC_WARNING_HOUR, minute=config.DOC_WARNING_MINUTE, tzinfo=ZoneInfo(config.TIMEZONE)),
    )
    application.job_queue.run_daily(
        daily_report_job,
        time=dt.time(hour=config.DAILY_REPORT_HOUR, minute=config.DAILY_REPORT_MINUTE, tzinfo=ZoneInfo(config.TIMEZONE)),
    )
    application.job_queue.run_daily(
        backup_job,
        time=dt.time(hour=config.BACKUP_HOUR, minute=config.BACKUP_MINUTE, tzinfo=ZoneInfo(config.TIMEZONE)),
    )

    if config.WEBHOOK_URL:
        port = int(config.PORT or 8080)
        application.run_webhook(listen="0.0.0.0", port=port, webhook_url=config.WEBHOOK_URL, url_path="webhook")
    else:
        if config.PORT:
            start_health_server(int(config.PORT))
        application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
