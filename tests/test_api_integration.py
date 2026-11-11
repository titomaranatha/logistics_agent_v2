from agent.agent import TransientLLMError
from tests.conftest import call, text


def test_full_flow_over_http(env):
    c = env.client
    env.script.push(call("w1", "create_pick_task", order_id="ORD-5", sku="SKU-1001", qty=20)).push(text("Task created"))
    r = c.post("/chat", json={"message": "pick 20 of SKU-1001 for ORD-5"}, headers=env.hdr("operator"))
    assert r.status_code == 200 and r.json()["status"] == "pending_approval"
    sid = r.json()["session_id"]
    r = c.post("/approve", json={"session_id": sid, "decisions": {"w1": True}}, headers=env.hdr("supervisor"))
    assert r.status_code == 200 and r.json()["status"] == "done"
    assert env.svc.wms.open_tasks()[0]["order_id"] == "ORD-5"
    assert env.svc.wms.open_tasks()[0]["task_id"]


def test_task_attributed_to_requester_and_audit_records_approver(env):
    env.script.push(call("w1", "create_pick_task", order_id="ORD-6", sku="SKU-1001", qty=5)).push(text("ok"))
    sid = env.client.post("/chat", json={"message": "x"}, headers=env.hdr("operator", "alice")).json()["session_id"]
    env.client.post("/approve", json={"session_id": sid, "decisions": {"w1": True}}, headers=env.hdr("supervisor", "bob"))
    audit = env.client.get("/audit", headers=env.hdr("admin")).json()
    exec_ev = [e for e in audit if e["event"] == "tool_call" and e["tool"] == "create_pick_task"][0]
    assert exec_ev["actor"] == "bob"
    from sqlalchemy import select

    from agent.db import PickTask
    with env.svc.Session() as s:
        assert s.scalar(select(PickTask.created_by)) == "alice"


def test_health_ready_and_degraded(env, monkeypatch):
    assert env.client.get("/health/live").json() == {"ok": True}
    assert env.client.get("/health/ready").json() == {"database": "ok", "redis": "ok"}
    monkeypatch.setattr(env.redis, "ping", lambda: (_ for _ in ()).throw(ConnectionError()))
    r = env.client.get("/health/ready")
    assert r.status_code == 503 and r.json()["redis"] == "down"


def test_metrics_and_request_id(env):
    env.script.push(call("1", "check_inventory", sku="SKU-1001")).push(text("260 available"))
    r = env.client.post("/chat", json={"message": "stock?"}, headers={**env.hdr("viewer"), "x-request-id": "req-123"})
    assert r.headers["x-request-id"] == "req-123"
    m = env.client.get("/metrics").text
    assert 'agent_tool_calls_total{outcome="ok",tool="check_inventory"}' in m
    assert "http_request_duration_seconds_bucket" in m and "agent_llm_duration_seconds" in m


def test_audit_trail_carries_request_id(env):
    env.script.push(call("1", "check_inventory", sku="SKU-1001")).push(text("260 available"))
    env.client.post("/chat", json={"message": "x"}, headers={**env.hdr("viewer", "v1"), "x-request-id": "rid-9"})
    ev = env.client.get("/audit", headers=env.hdr("admin")).json()
    assert any(e["request_id"] == "rid-9" and e["actor"] == "v1" and e["tool"] == "check_inventory" for e in ev)


def test_llm_outage_returns_503_with_retry_after(env):
    for _ in range(3):
        env.script.push_error(TransientLLMError("down"))
    r = env.client.post("/chat", json={"message": "hi"}, headers=env.hdr("operator"))
    assert r.status_code == 503 and r.headers["retry-after"]


def test_proposals_four_eyes_over_http(env):
    p = env.svc.proposals.create("book_dock", dict(dock="D1", date="2026-11-12", slot="09:00-10:00", carrier="DHL"),
                                 env.principal("supervisor", "sup-a"))
    # proposer (non-admin) cannot approve own proposal; a different supervisor can
    assert env.client.post(f"/proposals/{p['id']}/decision", json={"approve": True}, headers=env.hdr("supervisor", "sup-a")).status_code == 403
    r = env.client.post(f"/proposals/{p['id']}/decision", json={"approve": True}, headers=env.hdr("supervisor", "sup-b"))
    assert r.status_code == 200 and r.json()["status"] == "EXECUTED"
    assert env.client.post(f"/proposals/{p['id']}/decision", json={"approve": True}, headers=env.hdr("supervisor", "sup-b")).status_code == 409
    assert env.svc.wms.dock_schedule("2026-11-12")[0]["carrier"] == "DHL"
