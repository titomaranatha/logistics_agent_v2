"""PostgreSQL-first data layer (SQLAlchemy 2). SQLite is supported for local dev/tests only."""
import time
import uuid

from sqlalchemy import JSON, Float, Integer, String, UniqueConstraint, create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .observability import request_id_var


class Base(DeclarativeBase):
    pass


class Inventory(Base):
    __tablename__ = "inventory"
    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(32), index=True)
    location: Mapped[str] = mapped_column(String(32))
    on_hand: Mapped[int] = mapped_column(Integer)
    reserved: Mapped[int] = mapped_column(Integer, default=0)


class PickTask(Base):
    __tablename__ = "pick_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    idem: Mapped[str] = mapped_column(String(64), unique=True)
    order_id: Mapped[str] = mapped_column(String(64), index=True)
    sku: Mapped[str] = mapped_column(String(32))
    qty: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="OPEN", index=True)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class DockBooking(Base):
    __tablename__ = "dock_bookings"
    __table_args__ = (UniqueConstraint("dock", "date", "slot", name="uq_dock_slot"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    idem: Mapped[str] = mapped_column(String(64), unique=True)
    dock: Mapped[str] = mapped_column(String(16))
    date: Mapped[str] = mapped_column(String(10), index=True)
    slot: Mapped[str] = mapped_column(String(16))
    carrier: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(64))


class Proposal(Base):
    """A write action awaiting human approval (used by MCP clients and any async approval flow)."""
    __tablename__ = "proposals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    tool: Mapped[str] = mapped_column(String(64))
    args: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)  # PENDING/APPROVED/EXECUTED/FAILED/REJECTED
    proposed_by: Mapped[str] = mapped_column(String(64))
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[float] = mapped_column(Float, default=time.time, index=True)
    request_id: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(32))
    event: Mapped[str] = mapped_column(String(32))
    tool: Mapped[str | None] = mapped_column(String(64), nullable=True)
    args: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ok: Mapped[bool] = mapped_column(default=True)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)


def make_engine(url: str):
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
    return create_engine(url, pool_size=10, max_overflow=20, pool_pre_ping=True, pool_recycle=1800)


def make_sessionmaker(engine):
    return sessionmaker(engine, expire_on_commit=False)


def ping(engine):
    with engine.connect() as c:
        c.execute(text("SELECT 1"))


class Auditor:
    def __init__(self, Session):
        self.Session = Session

    def log(self, *, actor, role, event, tool=None, args=None, ok=True, detail=None):
        with self.Session.begin() as s:
            s.add(AuditEvent(request_id=request_id_var.get(), actor=actor, role=role, event=event,
                             tool=tool, args=args, ok=ok, detail=(detail or None) and str(detail)[:500]))

    def recent(self, limit=100):
        with self.Session() as s:
            rows = s.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)).all()
            return [{c.name: getattr(r, c.name) for c in AuditEvent.__table__.columns} for r in rows]


class WMS:
    """Warehouse repository. Reservations are transactional; idempotency is enforced by unique keys."""
    def __init__(self, Session):
        self.Session = Session

    def seed_demo(self):
        with self.Session.begin() as s:
            if s.scalar(select(Inventory.id).limit(1)):
                return
            s.add_all([Inventory(sku=k, location=l, on_hand=o, reserved=r) for k, l, o, r in [
                ("SKU-1001", "A-01-03", 240, 40), ("SKU-1001", "B-02-01", 60, 0),
                ("SKU-2002", "A-05-02", 15, 10), ("SKU-3003", "C-01-01", 0, 0)]])

    def inventory(self, sku):
        with self.Session() as s:
            rows = s.scalars(select(Inventory).where(Inventory.sku == sku).order_by(Inventory.location)).all()
        locs = [{"location": r.location, "on_hand": r.on_hand, "reserved": r.reserved,
                 "available": r.on_hand - r.reserved} for r in rows]
        return {"sku": sku, "found": bool(rows), "total_available": sum(l["available"] for l in locs), "locations": locs}

    def _task_dict(self, t, dedup=False):
        d = {"task_id": t.id, "order_id": t.order_id, "sku": t.sku, "qty": t.qty, "status": t.status}
        return d | ({"deduplicated": True} if dedup else {})

    def create_pick_task(self, order_id, sku, qty, idem, actor):
        try:
            with self.Session.begin() as s:
                existing = s.scalar(select(PickTask).where(PickTask.idem == idem))
                if existing:
                    return self._task_dict(existing, True)
                rows = s.scalars(select(Inventory).where(Inventory.sku == sku).order_by(Inventory.id)
                                 .with_for_update()).all()  # row locks on Postgres: no overselling
                avail = sum(r.on_hand - r.reserved for r in rows)
                if qty > avail:
                    raise ValueError(f"Insufficient stock for {sku}: requested {qty}, available {avail}")
                remaining = qty
                for r in rows:
                    take = min(remaining, r.on_hand - r.reserved)
                    r.reserved += take
                    remaining -= take
                    if not remaining:
                        break
                t = PickTask(idem=idem, order_id=order_id, sku=sku, qty=qty, created_by=actor)
                s.add(t)
                s.flush()
                return self._task_dict(t)
        except IntegrityError:  # concurrent duplicate with same idempotency key
            with self.Session() as s:
                t = s.scalar(select(PickTask).where(PickTask.idem == idem))
                if t:
                    return self._task_dict(t, True)
            raise

    def open_tasks(self):
        with self.Session() as s:
            rows = s.scalars(select(PickTask).where(PickTask.status == "OPEN").order_by(PickTask.id)).all()
            return [self._task_dict(r) for r in rows]

    def dock_schedule(self, date):
        with self.Session() as s:
            rows = s.scalars(select(DockBooking).where(DockBooking.date == date).order_by(DockBooking.slot)).all()
            return [{"dock": r.dock, "slot": r.slot, "carrier": r.carrier} for r in rows]

    def book_dock(self, dock, date, slot, carrier, idem, actor):
        def dict_(b, dedup=False):
            return {"booking_id": b.id, "dock": b.dock, "date": b.date, "slot": b.slot, "carrier": b.carrier} | (
                {"deduplicated": True} if dedup else {})
        try:
            with self.Session.begin() as s:
                ex = s.scalar(select(DockBooking).where(DockBooking.idem == idem))
                if ex:
                    return dict_(ex, True)
                b = DockBooking(idem=idem, dock=dock, date=date, slot=slot, carrier=carrier, created_by=actor)
                s.add(b)
                s.flush()
                return dict_(b)
        except IntegrityError:
            with self.Session() as s:
                ex = s.scalar(select(DockBooking).where(DockBooking.idem == idem))
                if ex:
                    return dict_(ex, True)
            raise ValueError(f"{dock} is already booked on {date} at {slot}") from None
