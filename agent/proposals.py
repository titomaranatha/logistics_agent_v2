"""Durable approval queue. Used by MCP clients; enforces four-eyes (proposer cannot approve own action unless admin)."""
from sqlalchemy import select, update

from .db import Proposal
from .observability import APPROVALS
from .tools import ToolContext


class ProposalService:
    def __init__(self, Session, tools, runner, auditor):
        self.Session, self.tools, self.runner, self.auditor = Session, tools, runner, auditor

    @staticmethod
    def _d(p):
        return {"id": p.id, "tool": p.tool, "args": p.args, "status": p.status, "proposed_by": p.proposed_by,
                "decided_by": p.decided_by, "result": p.result}

    def create(self, tool_name, args, principal):
        tool = self.tools.get(tool_name)
        if not tool or not tool.write:
            raise ValueError(f"{tool_name} is not a write tool")
        if not principal.can(tool.permission):
            raise PermissionError(tool.permission)
        validated = tool.model(**args).model_dump()
        with self.Session.begin() as s:
            p = Proposal(tool=tool_name, args=validated, proposed_by=principal.sub)
            s.add(p)
            s.flush()
            out = self._d(p)
        self.auditor.log(actor=principal.sub, role=principal.role, event="proposed", tool=tool_name, args=validated)
        return out

    def list(self, principal, status=None):
        with self.Session() as s:
            q = select(Proposal).order_by(Proposal.created_at)
            if status:
                q = q.where(Proposal.status == status)
            if not principal.can("actions:approve"):
                q = q.where(Proposal.proposed_by == principal.sub)
            return [self._d(p) for p in s.scalars(q).all()]

    def decide(self, pid, approve, principal):
        if not principal.can("actions:approve"):
            raise PermissionError("actions:approve")
        with self.Session() as s:
            p = s.get(Proposal, pid)
            if not p:
                raise KeyError(pid)
            tool, proposer, args = self.tools[p.tool], p.proposed_by, dict(p.args)
        if proposer == principal.sub and principal.role != "admin":
            raise PermissionError("four-eyes: you cannot approve your own proposal")
        if approve and not principal.can(tool.permission):
            raise PermissionError(tool.permission)
        new = "APPROVED" if approve else "REJECTED"
        with self.Session.begin() as s:   # atomic claim: only one decision can win
            n = s.execute(update(Proposal).where(Proposal.id == pid, Proposal.status == "PENDING")
                          .values(status=new, decided_by=principal.sub)).rowcount
        if n != 1:
            raise ValueError("Proposal already decided")
        APPROVALS.labels("approved" if approve else "rejected").inc()
        self.auditor.log(actor=principal.sub, role=principal.role, event=new.lower(), tool=tool.name, args=args)
        result = None
        if approve:
            ctx = ToolContext(actor=principal.sub, role=principal.role, request_key=f"proposal:{pid}", requested_by=proposer)
            result = self.runner.run(tool, tool.model(**args), ctx)
        with self.Session.begin() as s:
            row = s.get(Proposal, pid)
            if approve:
                row.status = "EXECUTED" if result["ok"] else "FAILED"
            row.result = result
            out = self._d(row)
        return out
