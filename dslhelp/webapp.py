from __future__ import annotations
import html
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from . import config
from .core import local_today
from .db import init_db, session_scope
from .models import AuditLog, Driver, Group, Load, Ticket, Truck

logger = logging.getLogger("dslhelp.webapp")

if not config.WEB_PANEL_TOKEN:
    logger.warning(
        "WEB_PANEL_TOKEN is not set — the dashboard is running UNPROTECTED and exposes "
        "driver names and locations. Set WEB_PANEL_TOKEN before deploying this publicly."
    )


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="DSL HELP Dashboard", lifespan=_lifespan)


PAGE_CSS = """
<style>
  body { font-family: -apple-system, Segoe UI, Arial, sans-serif; background:#0b1220; color:#e6e9ef; margin:0; padding:24px; }
  h1 { font-size:20px; margin-bottom:4px; }
  nav { margin-bottom:16px; }
  nav a { color:#8ab4f8; margin-right:16px; text-decoration:none; font-size:14px; }
  table { width:100%; border-collapse:collapse; margin-top:4px; }
  th, td { text-align:left; padding:6px 10px; border-bottom:1px solid #223047; font-size:14px; }
  th { color:#9aa4b2; font-weight:600; }
  .card { background:#121a2b; border-radius:10px; padding:16px; margin-bottom:16px; }
  .meta { color:#9aa4b2; font-size:12px; margin-top:12px; }
</style>
"""

NAV_LINKS = [("Overview", ""), ("Drivers", "drivers"), ("Loads", "loads"), ("Tickets", "tickets"), ("Trucks", "trucks"), ("Audit log", "audit")]


def _nav(token: str) -> str:
    q = f"?token={html.escape(token)}" if token else ""
    links = "".join(f'<a href="/{path}{q}">{label}</a>' for label, path in NAV_LINKS)
    return f"<nav>{links}</nav>"


def _layout(title: str, body: str, token: str) -> str:
    return (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>DSL HELP — {html.escape(title)}</title>{PAGE_CSS}</head>"
        f"<body><h1>DSL HELP — {html.escape(title)}</h1>{_nav(token)}"
        f"<div class='card'>{body}</div>"
        f"<p class='meta'>Generated {local_today().isoformat()} · read-only</p>"
        f"</body></html>"
    )


def _check_token(request: Request) -> str:
    token = request.query_params.get("token", "")
    if config.WEB_PANEL_TOKEN and token != config.WEB_PANEL_TOKEN:
        raise HTTPException(status_code=403, detail="Noto'g'ri token. URL oxiriga ?token=... qo'shing.")
    return token


def _table(headers: list[str], rows: list[tuple]) -> str:
    if not rows:
        return "<p>Yo'q.</p>"
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body_rows = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>" for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body_rows}</tbody></table>"


@app.get("/", response_class=HTMLResponse)
async def overview(request: Request) -> str:
    token = _check_token(request)
    async with session_scope() as session:
        driver_count = len((await session.execute(select(Driver).where(Driver.active.is_(True)))).scalars().all())
        load_count = len((await session.execute(select(Load).where(Load.status != "invoiced"))).scalars().all())
        ticket_count = len((await session.execute(select(Ticket).where(Ticket.status != "done"))).scalars().all())
        truck_count = len((await session.execute(select(Truck))).scalars().all())

    body = (
        f"<p>Drivers: <b>{driver_count}</b></p>"
        f"<p>Active loads: <b>{load_count}</b></p>"
        f"<p>Open tickets: <b>{ticket_count}</b></p>"
        f"<p>Trucks: <b>{truck_count}</b></p>"
    )
    return _layout("Overview", body, token)


@app.get("/drivers", response_class=HTMLResponse)
async def drivers_page(request: Request) -> str:
    token = _check_token(request)
    async with session_scope() as session:
        drivers = (
            await session.execute(select(Driver).where(Driver.active.is_(True)).order_by(Driver.first_name))
        ).scalars().all()
        rows = []
        for d in drivers:
            group = await session.get(Group, d.group_id)
            loc = f"{d.last_lat:.4f},{d.last_lon:.4f}" if d.last_lat is not None else "-"
            rows.append((d.id, d.full_name, group.title or group.chat_id, d.language, loc))
    return _layout("Drivers", _table(["ID", "Name", "Group", "Lang", "Last location"], rows), token)


@app.get("/loads", response_class=HTMLResponse)
async def loads_page(request: Request) -> str:
    token = _check_token(request)
    async with session_scope() as session:
        loads = (
            await session.execute(select(Load).where(Load.status != "invoiced").order_by(Load.id.desc()))
        ).scalars().all()
        rows = []
        for ld in loads:
            driver = await session.get(Driver, ld.driver_id)
            rows.append((ld.id, ld.load_number or "-", ld.broker or "-", driver.full_name if driver else "-", ld.status))
    return _layout("Active loads", _table(["ID", "Load#", "Broker", "Driver", "Status"], rows), token)


@app.get("/tickets", response_class=HTMLResponse)
async def tickets_page(request: Request) -> str:
    token = _check_token(request)
    async with session_scope() as session:
        tickets = (
            await session.execute(select(Ticket).where(Ticket.status != "done").order_by(Ticket.id.desc()))
        ).scalars().all()
        rows = []
        for t in tickets:
            driver = await session.get(Driver, t.driver_id)
            rows.append(
                (t.id, t.department, t.category or "-", driver.full_name if driver else "-", t.status, "🔴" if t.urgent else "")
            )
    return _layout("Open tickets", _table(["ID", "Dept", "Category", "Driver", "Status", "Urgent"], rows), token)


@app.get("/trucks", response_class=HTMLResponse)
async def trucks_page(request: Request) -> str:
    token = _check_token(request)
    async with session_scope() as session:
        trucks = (await session.execute(select(Truck).order_by(Truck.number))).scalars().all()
        rows = []
        for t in trucks:
            driver = await session.get(Driver, t.driver_id) if t.driver_id else None
            next_pm = t.last_pm_mileage + t.pm_interval_miles
            rows.append((t.number, driver.full_name if driver else "-", t.mileage, next_pm))
    return _layout("Trucks", _table(["Truck", "Driver", "Mileage", "Next PM (mi)"], rows), token)


@app.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request) -> str:
    token = _check_token(request)
    async with session_scope() as session:
        entries = (
            await session.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(100))
        ).scalars().all()
        rows = [(e.created_at.strftime("%Y-%m-%d %H:%M"), e.actor_name, e.action, e.detail) for e in entries]
    return _layout("Audit log", _table(["When", "Actor", "Action", "Detail"], rows), token)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=config.WEB_PANEL_PORT)


if __name__ == "__main__":
    main()
