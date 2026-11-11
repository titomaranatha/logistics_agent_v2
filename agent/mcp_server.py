"""MCP server exposing the same governed tool registry to MCP clients (Claude Desktop, IDEs, other agents).
Reads execute directly; writes become durable *proposals* that a supervisor approves via the REST API.
Tools are only registered if the configured service principal's role allows them."""
import os
import uuid

from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from .config import Settings
from .security import Principal
from .tools import ToolContext
from .wiring import Services, build_services


def build_mcp_server(svc: Services, principal: Principal) -> MCPServer:
    mcp = MCPServer("logistics-ops", instructions="Warehouse operations tools. Write actions are proposals requiring supervisor approval.")

    def call(name, **kw):
        tool = svc.tools[name]
        if not principal.can(tool.permission):
            return {"ok": False, "error": {"type": "forbidden", "message": f"missing {tool.permission}", "retryable": False}}
        try:
            args = tool.model(**kw)
        except ValidationError as e:
            return {"ok": False, "error": {"type": "invalid_input", "retryable": True,
                                           "message": "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())}}
        if tool.write:
            p = svc.proposals.create(name, args.model_dump(), principal)
            return {"ok": True, "data": {"proposal": p, "message": "Proposal created; awaiting supervisor approval."}}
        return svc.runner.run(tool, args, ToolContext(actor=principal.sub, role=principal.role, request_key=uuid.uuid4().hex))

    def check_inventory(sku: str) -> dict:
        return call("check_inventory", sku=sku)

    def list_open_pick_tasks() -> dict:
        return call("list_open_pick_tasks")

    def track_shipment(tracking_no: str) -> dict:
        return call("track_shipment", tracking_no=tracking_no)

    def get_dock_schedule(date: str) -> dict:
        return call("get_dock_schedule", date=date)

    def search_knowledge(query: str) -> dict:
        return call("search_knowledge", query=query)

    def create_pick_task(order_id: str, sku: str, qty: int) -> dict:
        return call("create_pick_task", order_id=order_id, sku=sku, qty=qty)

    def book_dock(dock: str, date: str, slot: str, carrier: str) -> dict:
        return call("book_dock", dock=dock, date=date, slot=slot, carrier=carrier)

    for fn in (check_inventory, list_open_pick_tasks, track_shipment, get_dock_schedule, search_knowledge,
               create_pick_task, book_dock):
        tool = svc.tools[fn.__name__]
        if principal.can(tool.permission):
            mcp.add_tool(fn, name=tool.name, description=tool.description)
    return mcp


if __name__ == "__main__":  # python -m agent.mcp_server  (stdio)
    services = build_services(Settings.from_env())
    who = Principal(os.getenv("MCP_USER", "mcp-service"), os.getenv("MCP_ROLE", "operator"))
    build_mcp_server(services, who).run("stdio")
