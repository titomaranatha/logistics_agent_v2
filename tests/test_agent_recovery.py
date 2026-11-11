"""Agent reasoning, verification and recovery behaviour (scripted model)."""
from agent.agent import TransientLLMError
from tests.conftest import call, text


def new_state(env, role="operator"):
    return env.store.create(env.principal(role))


def test_read_tool_then_grounded_answer(env):
    env.script.push(call("1", "check_inventory", sku="SKU-1001")).push(text("260 available across 2 locations"))
    r = env.agent.chat(new_state(env), "stock for SKU-1001?")
    assert r["status"] == "done" and r["grounded"] and r["tools_called"] == ["check_inventory"]


def test_ungrounded_answer_is_repaired(env):
    env.script.push(call("1", "check_inventory", sku="SKU-1001")).push(text("300 available")).push(text("260 available"))
    r = env.agent.chat(new_state(env), "stock for SKU-1001?")
    assert r["status"] == "done" and r["grounded"] and "260" in r["reply"] and len(env.script.calls) == 3


def test_persistent_ungrounded_answer_is_flagged_not_trusted(env):
    env.script.push(call("1", "check_inventory", sku="SKU-1001")).push(text("300 available")).push(text("300 available"))
    r = env.agent.chat(new_state(env), "stock?")
    assert r["grounded"] is False and "Unverified figures" in r["reply"]


def test_llm_transient_errors_are_retried(env):
    env.script.push_error(TransientLLMError("429")).push_error(TransientLLMError("503")).push(text("hello"))
    assert env.agent.chat(new_state(env), "hi")["status"] == "done"


def test_llm_outage_degrades_gracefully(env):
    for _ in range(3):
        env.script.push_error(TransientLLMError("down"))
    r = env.agent.chat(new_state(env), "hi")
    assert r["status"] == "error" and r["code"] == "llm_unavailable"


def test_write_needs_approval_executes_once_and_reserves_stock(env):
    env.script.push(call("w1", "create_pick_task", order_id="ORD-9", sku="SKU-1001", qty=20)).push(text("Created task"))
    st = new_state(env)
    r = env.agent.chat(st, "pick 20 of SKU-1001 for ORD-9")
    assert r["status"] == "pending_approval" and env.svc.wms.open_tasks() == []
    r = env.agent.resolve(st, env.principal("supervisor"), {"w1": True})
    assert r["status"] == "done" and len(env.svc.wms.open_tasks()) == 1
    assert env.svc.wms.inventory("SKU-1001")["total_available"] == 240
    assert env.agent.resolve(st, env.principal("supervisor"), {"w1": True})["code"] == "nothing_pending"
    assert len(env.svc.wms.open_tasks()) == 1


def test_rejection_executes_nothing(env):
    env.script.push(call("w1", "create_pick_task", order_id="ORD-9", sku="SKU-1001", qty=20)).push(text("Cancelled"))
    st = new_state(env); env.agent.chat(st, "x")
    env.agent.resolve(st, env.principal("supervisor"), {"w1": False})
    assert env.svc.wms.open_tasks() == [] and env.svc.wms.inventory("SKU-1001")["total_available"] == 260


def test_business_rule_failure_is_audited_and_reported(env):
    env.script.push(call("w1", "create_pick_task", order_id="ORD-1", sku="SKU-2002", qty=500)).push(text("Only 5 in stock"))
    st = new_state(env); env.agent.chat(st, "x")
    assert env.agent.resolve(st, env.principal("supervisor"), {"w1": True})["status"] == "done"
    ev = [e for e in env.svc.auditor.recent() if e["tool"] == "create_pick_task" and e["event"] == "tool_call"]
    assert ev and ev[0]["ok"] is False and "Insufficient" in ev[0]["detail"]


def test_repeated_tool_failures_escalate_to_human(env):
    for i in range(3):
        env.script.push(call(str(i), "check_inventory", sku="bad"))
    r = env.agent.chat(new_state(env), "x")
    assert r["status"] == "escalated"


def test_step_limit(env):
    for i in range(10):
        env.script.push(call(str(i), "list_open_pick_tasks"))
    assert env.agent.chat(new_state(env), "loop")["code"] == "step_limit"


def test_policy_question_uses_retrieval(env):
    env.script.push(call("1", "search_knowledge", query="damaged pallet")).push(text("Quarantine it in bay Q-01 (SOP-REC-02)."))
    r = env.agent.chat(new_state(env), "what do I do with a damaged pallet?")
    assert r["status"] == "done" and r["tools_called"] == ["search_knowledge"]
