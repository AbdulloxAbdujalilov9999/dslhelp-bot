from __future__ import annotations
import datetime as dt
from typing import Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def ensure_aware(value: dt.datetime) -> dt.datetime:
    """SQLite drops tzinfo on round-trip (unlike Postgres/asyncpg); re-attach UTC so
    comparisons against utcnow() don't blow up with naive-vs-aware TypeErrors."""
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value


class Base(DeclarativeBase):
    pass


class Group(Base):
    """A Telegram chat: either a department group or a driver group."""

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    # department code (dispatch/maintenance/eld/safety/accounting/hr) if this is a department group,
    # "driver" if this is a driver group, NULL if unbound.
    kind: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    drivers: Mapped[list["Driver"]] = relationship(back_populates="group")


class Driver(Base):
    __tablename__ = "drivers"
    __table_args__ = (UniqueConstraint("telegram_user_id", "group_id", name="uq_driver_user_group"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    first_name: Mapped[str] = mapped_column(String(128))
    last_name: Mapped[str] = mapped_column(String(128), default="")
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    language: Mapped[str] = mapped_column(String(8), default="uz")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_location_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    group: Mapped["Group"] = relationship(back_populates="drivers")

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    department: Mapped[str] = mapped_column(String(32), index=True)
    category: Mapped[str] = mapped_column(String(64), default="")
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.id"))
    driver_group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    dept_group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    truck_id: Mapped[Optional[int]] = mapped_column(ForeignKey("trucks.id"), nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="open")  # open, working, done
    urgent: Mapped[bool] = mapped_column(Boolean, default=False)
    dept_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reminder_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[str] = mapped_column(String(128), default="")

    driver: Mapped["Driver"] = relationship()


class TicketMedia(Base):
    __tablename__ = "ticket_media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"))
    file_id: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(16), default="photo")
    caption: Mapped[str] = mapped_column(String(512), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Load(Base):
    """Load card: one record per load, from dispatch assignment through invoicing."""

    __tablename__ = "loads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.id"))
    driver_group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    status: Mapped[str] = mapped_column(String(16), default="assigned")
    # assigned -> picked_up -> delivered -> pod -> invoiced
    load_number: Mapped[str] = mapped_column(String(64), default="")
    broker: Mapped[str] = mapped_column(String(128), default="")
    pu_address: Mapped[str] = mapped_column(String(256), default="")
    pu_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    del_address: Mapped[str] = mapped_column(String(256), default="")
    del_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    bol_file_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    pod_file_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    pod_trailer: Mapped[str] = mapped_column(String(64), default="")  # trailer number, or "BOBTAIL"
    pu_reminder_2h: Mapped[bool] = mapped_column(Boolean, default=False)
    pu_reminder_30m: Mapped[bool] = mapped_column(Boolean, default=False)
    pu_late_alert: Mapped[bool] = mapped_column(Boolean, default=False)
    del_reminder_2h: Mapped[bool] = mapped_column(Boolean, default=False)
    del_reminder_30m: Mapped[bool] = mapped_column(Boolean, default=False)
    del_late_alert: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    driver: Mapped["Driver"] = relationship()


class PTIRequest(Base):
    __tablename__ = "pti_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    maintenance_group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class PTIRequestDriver(Base):
    __tablename__ = "pti_request_drivers"
    __table_args__ = (UniqueConstraint("pti_request_id", "driver_id", name="uq_pti_req_driver"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pti_request_id: Mapped[int] = mapped_column(ForeignKey("pti_requests.id"))
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.id"))
    truck_id: Mapped[Optional[int]] = mapped_column(ForeignKey("trucks.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending, submitted, approved, redo
    prompt_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    driver: Mapped["Driver"] = relationship()


class PTIPhoto(Base):
    __tablename__ = "pti_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pti_request_driver_id: Mapped[int] = mapped_column(ForeignKey("pti_request_drivers.id"))
    file_id: Mapped[str] = mapped_column(String(256))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.id"))
    doc_type: Mapped[str] = mapped_column(String(32))  # CDL, MEDICAL, ...
    expires_on: Mapped[dt.date] = mapped_column(Date)
    file_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="approved")  # pending, approved, rejected
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    driver: Mapped["Driver"] = relationship()


class Truck(Base):
    __tablename__ = "trucks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    driver_id: Mapped[Optional[int]] = mapped_column(ForeignKey("drivers.id"), nullable=True)
    mileage: Mapped[int] = mapped_column(Integer, default=0)
    last_pm_mileage: Mapped[int] = mapped_column(Integer, default=0)
    last_pm_date: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)
    pm_interval_miles: Mapped[int] = mapped_column(Integer, default=10000)
    pm_interval_days: Mapped[int] = mapped_column(Integer, default=90)
    pm_alert_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CostEntry(Base):
    __tablename__ = "cost_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"))
    truck_id: Mapped[Optional[int]] = mapped_column(ForeignKey("trucks.id"), nullable=True)
    work_type: Mapped[str] = mapped_column(String(128), default="")
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SafetyRecord(Base):
    __tablename__ = "safety_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"))
    category: Mapped[str] = mapped_column(String(64), default="")
    outcome_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CSAScore(Base):
    __tablename__ = "csa_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    score: Mapped[float] = mapped_column(Float)
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Receipt(Base):
    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.id"))
    kind: Mapped[str] = mapped_column(String(16))  # fuel, scale, lumper
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    file_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    driver: Mapped["Driver"] = relationship()


class Staff(Base):
    """A department-group member who self-registered as staff of that department.

    Separate from Driver: a person can be staff of exactly one department group,
    distinct from the per-group Driver registration used in driver groups.
    """

    __tablename__ = "staff"
    __table_args__ = (UniqueConstraint("telegram_user_id", "department", name="uq_staff_user_department"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    department: Mapped[str] = mapped_column(String(32), index=True)
    first_name: Mapped[str] = mapped_column(String(128))
    last_name: Mapped[str] = mapped_column(String(128), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class ConversationState(Base):
    """Per-user scratch state for multi-step flows (intake forms, PTI submission).

    Keyed by telegram_user_id; only that user's messages advance their own state.
    Expires after STATE_TTL_MINUTES (checked at read time).
    """

    __tablename__ = "conversation_state"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[int] = mapped_column(BigInteger)
    actor_name: Mapped[str] = mapped_column(String(128), default="")
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
