"""Behavioural evaluation harness.
  python -m evals.run_evals            # live model (needs ANTHROPIC_API_KEY); fails if pass-rate < --min-pass
Programmatic use (CI/tests): evaluate(cases, make_llm) with a scripted model to validate the harness itself."""
import argparse
import json
import os
import sys
import tempfile

import fakeredis

from agent.agent import LogisticsAgent, default_llm
from agent.config import Settings
from agent.security import Principal
from agent.sessions import SessionStore
from agent.wiring import build_services

CASES = os.path.join(os.path.dirname(__file__), "cases.json")


def check(case, result, tools_called):
    fails = []
    for t in case.get("expect_tools", []):
        if t not in tools_called: fails.append(f"expected tool {t}")
    for t in case.get("forbid_tools", []):
        if t in tools_called: fails.append(f"forbidden tool {t} was called")
    if "expect_status" in case and result["status"] != case["expect_status"]:
        fails.append(f"status {result['status']} != {case['expect_status']}")
    reply = result.get("reply", "").lower()
    for needle in case.get("reply_must_contain", []):
        if needle.lower() not in reply: fails.append(f"reply missing '{needle}'")
    if result.get("grounded") is False: fails.append("ungrounded figures in reply")
    return fails


def evaluate(cases, make_llm, tmpdir=None):
    tmpdir = tmpdir or tempfile.mkdtemp()
    results = []
    for case in cases:
        s = Settings(database_url=f"sqlite:///{tmpdir}/{case['id']}.db", jwt_secret="e" * 32, seed_demo=True,
                     knowledge_dir=os.path.join(os.path.dirname(__file__), "..", "knowledge"), llm_backoff_base=0)
        svc = build_services(s)
        agent = LogisticsAgent(svc.tools, svc.runner, svc.auditor, s, llm=make_llm(case), sleep=lambda _: None)
        state = SessionStore(fakeredis.FakeRedis(decode_responses=True)).create(Principal("eval", case["role"]))
        res = agent.chat(state, case["user"])
        fails = check(case, res, res["tools_called"])
        results.append({"id": case["id"], "passed": not fails, "failures": fails})
    return {"pass_rate": sum(r["passed"] for r in results) / len(results), "results": results}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--min-pass", type=float, default=0.9)
    a = ap.parse_args()
    cases = json.load(open(CASES))
    report = evaluate(cases, lambda c: default_llm(Settings()))
    print(json.dumps(report, indent=2))
    sys.exit(0 if report["pass_rate"] >= a.min_pass else 1)
