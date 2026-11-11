"""Race conditions: run meaningfully on PostgreSQL (TEST_DB=postgres); idempotency also checked on SQLite."""
import threading

import pytest
from sqlalchemy import func, select

from agent.db import PickTask


def hammer(n, fn):
    out, errs, barrier = [], [], threading.Barrier(n)
    def w(i):
        barrier.wait()
        try: out.append(fn(i))
        except Exception as e: errs.append(e)
    ts = [threading.Thread(target=w, args=(i,)) for i in range(n)]
    [t.start() for t in ts]; [t.join() for t in ts]
    return out, errs


def test_same_idempotency_key_creates_exactly_one_task(env):
    out, errs = hammer(16, lambda i: env.svc.wms.create_pick_task("ORD-1", "SKU-1001", 10, "same-key", "u"))
    assert not errs and len({o["task_id"] for o in out}) == 1
    assert env.svc.wms.inventory("SKU-1001")["total_available"] == 250        # reserved once


def test_no_overselling_under_contention(env, is_postgres):
    if not is_postgres:
        pytest.skip("row-level locking semantics need PostgreSQL")
    out, errs = hammer(20, lambda i: env.svc.wms.create_pick_task(f"ORD-{i}", "SKU-2002", 1, f"k{i}", "u"))
    assert len(out) == 5 and len(errs) == 15 and all(isinstance(e, ValueError) for e in errs)   # 15 - 10 reserved = 5 available
    assert env.svc.wms.inventory("SKU-2002")["total_available"] == 0


def test_concurrent_dock_booking_has_single_winner(env):
    out, errs = hammer(12, lambda i: env.svc.wms.book_dock("D1", "2026-11-12", "09:00-10:00", f"C{i}", f"k{i}", "u"))
    assert len(out) == 1 and len(errs) == 11 and all(isinstance(e, ValueError) for e in errs)


def test_concurrent_proposal_decisions_single_execution(env):
    p = env.svc.proposals.create("create_pick_task", dict(order_id="ORD-3", sku="SKU-1001", qty=10), env.principal("operator"))
    out, errs = hammer(10, lambda i: env.svc.proposals.decide(p["id"], True, env.principal("supervisor", f"s{i}")))
    assert len(out) == 1 and len(errs) == 9
    with env.svc.Session() as s:
        assert s.scalar(select(func.count()).select_from(PickTask)) == 1
