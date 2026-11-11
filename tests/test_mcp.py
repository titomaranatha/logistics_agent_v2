import asyncio
import json

import pytest
from mcp.client import Client

from agent.mcp_server import build_mcp_server


def run(env, role, fn, sub=None):
    server = build_mcp_server(env.svc, env.principal(role, sub))
    async def go():
        async with Client(server) as client:
            return await fn(client)
    return asyncio.run(go())


def payload(res):
    if getattr(res, "structured_content", None):
        sc = res.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(res.content[0].text)


def test_tools_are_filtered_by_role(env):
    names = lambda role: sorted(t.name for t in run(env, role, lambda c: c.list_tools()).tools)
    assert names("viewer") == ["check_inventory", "get_dock_schedule", "list_open_pick_tasks", "search_knowledge", "track_shipment"]
    assert "create_pick_task" in names("operator") and "book_dock" in names("operator")


def test_read_tool_and_retrieval_over_mcp(env):
    r = payload(run(env, "viewer", lambda c: c.call_tool("check_inventory", {"sku": "SKU-1001"})))
    assert r["ok"] and r["data"]["total_available"] == 260
    r = payload(run(env, "viewer", lambda c: c.call_tool("search_knowledge", {"query": "carrier claim"})))
    assert r["data"]["results"][0]["source"] == "receiving_damaged_goods.md"


def test_write_over_mcp_creates_proposal_not_action_then_supervisor_executes(env):
    r = payload(run(env, "operator", lambda c: c.call_tool("create_pick_task", {"order_id": "ORD-8", "sku": "SKU-1001", "qty": 10}), "op-1"))
    pid = r["data"]["proposal"]["id"]
    assert r["data"]["proposal"]["status"] == "PENDING" and env.svc.wms.open_tasks() == []
    out = env.svc.proposals.decide(pid, True, env.principal("supervisor", "sup-1"))
    assert out["status"] == "EXECUTED" and env.svc.wms.open_tasks()[0]["order_id"] == "ORD-8"
    with pytest.raises(ValueError):                        # double decision
        env.svc.proposals.decide(pid, True, env.principal("supervisor", "sup-2"))


def test_invalid_input_over_mcp_is_rejected(env):
    res = run(env, "operator", lambda c: c.call_tool("create_pick_task", {"order_id": "ORD-8", "sku": "SKU-1001", "qty": -5}))
    text = json.dumps(payload(res)) if not res.is_error else str(res.content)
    assert "qty" in text or res.is_error
    assert env.svc.proposals.list(env.principal("supervisor")) == []
