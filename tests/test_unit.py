import pytest
from pydantic import ValidationError

from agent.rag import KnowledgeBase
from agent.reliability import grounding_issues
from agent.runner import ToolRunner
from agent.security import AuthError, decode_token, issue_token
from agent.tools import BookDock, CheckInventory, PickTask, Tool, ToolContext
from tests.conftest import KB_DIR, SECRET


# ---- RAG ----
def test_rag_finds_relevant_sop_and_ignores_offtopic():
    kb = KnowledgeBase.load(KB_DIR)
    assert kb.search("what do I do with a damaged pallet")[0]["source"] == "receiving_damaged_goods.md"
    assert kb.search("who approves a stock variance")[0]["section"] == "Recount and adjustment rules"
    assert kb.search("how do I bake bread") == []          # no hallucination fodder


# ---- grounding ----
def test_grounding_flags_unsupported_figures_only():
    facts = ['{"total_available": 260, "locations": [{"available": 200}, {"available": 60}]}']
    assert grounding_issues("260 available (200 + 60)", facts, []) == []
    assert grounding_issues("About 300 available", facts, []) == ["300"]
    assert grounding_issues("pick 20 units", facts, ["pick 20 units please"]) == []


# ---- validation ----
@pytest.mark.parametrize("model,bad", [
    (CheckInventory, {"sku": "SKU-1001' OR 1=1--"}), (CheckInventory, {"sku": "sku-1"}),
    (PickTask, {"order_id": "ORD-1", "sku": "SKU-1001", "qty": 0}),
    (PickTask, {"order_id": "ORD-1", "sku": "SKU-1001", "qty": 10**6}),
    (BookDock, {"dock": "D1", "date": "tomorrow", "slot": "09:00-10:00", "carrier": "DHL"})])
def test_tool_inputs_are_strictly_validated(model, bad):
    with pytest.raises(ValidationError):
        model(**bad)


# ---- security primitives ----
def test_jwt_rejects_tampered_wrong_issuer_and_alg_none():
    good = issue_token(SECRET, "u", "operator")
    assert decode_token(SECRET, good).role == "operator"
    for bad in (good[:-2] + "xx", issue_token("other-secret-" + "y" * 24, "u", "admin"),
                issue_token(SECRET, "u", "operator", issuer="evil"), issue_token(SECRET, "u", "root"),
                issue_token(SECRET, "u", "operator", ttl=-10), "a.b.c"):
        with pytest.raises(AuthError):
            decode_token(SECRET, bad)


# ---- runner never leaks internals ----
def test_runner_hides_internal_errors_and_types_business_errors(env):
    def boom(a, c): raise RuntimeError("password=hunter2 host=db.internal")
    def rule(a, c): raise ValueError("Insufficient stock")
    ctx = ToolContext(actor="u", role="operator", request_key="k")
    from agent.tools import Empty
    r = ToolRunner(env.svc.auditor)
    res = r.run(Tool("t", "d", Empty, boom, "tasks:read"), Empty(), ctx)
    assert "hunter2" not in str(res) and res["error"]["retryable"] is True
    res = r.run(Tool("t2", "d", Empty, rule, "tasks:read"), Empty(), ctx)
    assert res["error"] == {"type": "business_rule", "message": "Insufficient stock", "retryable": False}


# ---- repository ----
def test_pick_reserves_stock_and_is_idempotent(env):
    w = env.svc.wms
    assert w.inventory("SKU-1001")["total_available"] == 260
    a = w.create_pick_task("ORD-1", "SKU-1001", 230, "k1", "u")
    b = w.create_pick_task("ORD-1", "SKU-1001", 230, "k1", "u")
    assert a["task_id"] == b["task_id"] and b["deduplicated"]
    assert w.inventory("SKU-1001")["total_available"] == 30
    with pytest.raises(ValueError, match="Insufficient"):
        w.create_pick_task("ORD-2", "SKU-1001", 31, "k2", "u")


def test_dock_double_booking_rejected(env):
    w = env.svc.wms
    w.book_dock("D1", "2026-11-12", "09:00-10:00", "DHL", "a", "u")
    with pytest.raises(ValueError, match="already booked"):
        w.book_dock("D1", "2026-11-12", "09:00-10:00", "FedEx", "b", "u")
