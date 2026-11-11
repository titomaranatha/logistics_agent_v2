import hashlib
import uuid
from dataclasses import dataclass
from typing import Callable, Type

from pydantic import BaseModel, Field


@dataclass
class ToolContext:
    actor: str
    role: str
    request_key: str = ""      # stable per proposed action => idempotency across retries/double-approvals
    requested_by: str = ""


@dataclass
class Tool:
    name: str
    description: str
    model: Type[BaseModel]
    handler: Callable
    permission: str
    write: bool = False

    def spec(self):
        return {"name": self.name, "description": self.description, "input_schema": self.model.model_json_schema()}


SKU = r"^SKU-\d{4}$"
DATE = r"^\d{4}-\d{2}-\d{2}$"


class CheckInventory(BaseModel):
    sku: str = Field(pattern=SKU, description="SKU like SKU-1001")

class Empty(BaseModel):
    pass

class Track(BaseModel):
    tracking_no: str = Field(pattern=r"^[A-Za-z0-9-]{6,40}$")

class DockDate(BaseModel):
    date: str = Field(pattern=DATE, description="YYYY-MM-DD")

class Query(BaseModel):
    query: str = Field(min_length=3, max_length=200, description="Natural-language question about warehouse procedure")

class PickTask(BaseModel):
    order_id: str = Field(pattern=r"^[A-Za-z0-9-]{3,40}$")
    sku: str = Field(pattern=SKU)
    qty: int = Field(gt=0, le=10000)

class BookDock(BaseModel):
    dock: str = Field(pattern=r"^[A-Za-z0-9-]{1,16}$")
    date: str = Field(pattern=DATE)
    slot: str = Field(pattern=r"^\d{2}:\d{2}-\d{2}:\d{2}$", description="e.g. 09:00-10:00")
    carrier: str = Field(min_length=2, max_length=64)


def _idem(tool, ctx):
    return hashlib.sha256(f"{tool}|{ctx.request_key or uuid.uuid4().hex}".encode()).hexdigest()[:40]


def build_tools(wms, carrier, kb):
    def search_kb(a, ctx):
        hits = kb.search(a.query)
        if not hits:
            return {"results": [], "note": "No relevant SOP found. Do not invent a procedure; say it is not documented."}
        return {"results": [{k: h[k] for k in ("source", "section", "text")} for h in hits]}

    def pick(a, ctx):
        return wms.create_pick_task(a.order_id, a.sku, a.qty, _idem("pick", ctx), ctx.requested_by or ctx.actor)

    def dock(a, ctx):
        return wms.book_dock(a.dock, a.date, a.slot, a.carrier, _idem("dock", ctx), ctx.requested_by or ctx.actor)

    return {t.name: t for t in [
        Tool("check_inventory", "Stock on hand, reserved and available for a SKU, by location.",
             CheckInventory, lambda a, c: wms.inventory(a.sku), "inventory:read"),
        Tool("list_open_pick_tasks", "List all open pick tasks.", Empty, lambda a, c: wms.open_tasks(), "tasks:read"),
        Tool("track_shipment", "Carrier tracking status and ETA for a tracking number.",
             Track, lambda a, c: carrier.track(a.tracking_no), "shipments:read"),
        Tool("get_dock_schedule", "Dock bookings for a date.", DockDate, lambda a, c: wms.dock_schedule(a.date), "docks:read"),
        Tool("search_knowledge", "Search warehouse SOPs/policies (damaged goods, discrepancies, dock rules). "
             "Use for any 'how do we / what is the policy' question and cite the source file.",
             Query, search_kb, "kb:read"),
        Tool("create_pick_task", "Create a pick task for an order line and reserve stock. Requires human approval.",
             PickTask, pick, "tasks:write", write=True),
        Tool("book_dock", "Book a dock slot for a carrier. Requires human approval.",
             BookDock, dock, "docks:write", write=True),
    ]}
