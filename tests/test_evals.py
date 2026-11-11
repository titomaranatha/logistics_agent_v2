"""Validates the eval harness itself with scripted models: it must pass good behaviour and catch bad behaviour."""
import json

from evals.run_evals import CASES, evaluate
from tests.conftest import Script, call, text

CASE = {c["id"]: c for c in json.load(open(CASES))}


def llm_for(good: bool):
    def make(case):
        s = Script()
        cid = case["id"]
        if cid == "inventory_lookup":
            s.push(call("1", "check_inventory", sku="SKU-1001")).push(text("260 available" if good else "999 available")).push(text("999 available"))
        elif cid == "policy_question_uses_sop":
            (s.push(call("1", "search_knowledge", query="damaged pallet")).push(text("Move it to quarantine bay Q-01 (SOP-REC-02).")) if good
             else s.push(text("Just sign for it.")))
        elif cid == "undocumented_policy_not_invented":
            s.push(call("1", "search_knowledge", query="forklift certification policy")).push(text("That is not documented in our SOPs."))
        elif cid == "write_requires_approval":
            s.push(call("1", "create_pick_task", order_id="ORD-77", sku="SKU-1001", qty=20))
        elif cid == "viewer_cannot_write":
            s.push(text("I don't have permission to book docks."))  # write tools are not even offered to viewers
        elif cid == "ambiguous_request_asks_question":
            s.push(text("Which SKU should I check?"))
        return s
    return make


def test_harness_passes_correct_behaviour():
    report = evaluate(list(CASE.values()), llm_for(good=True))
    assert report["pass_rate"] == 1.0, report


def test_harness_catches_regressions():
    report = evaluate([CASE["inventory_lookup"], CASE["policy_question_uses_sop"]], llm_for(good=False))
    assert report["pass_rate"] == 0.0
    fails = " ".join(f for r in report["results"] for f in r["failures"])
    assert "ungrounded" in fails and "expected tool search_knowledge" in fails
