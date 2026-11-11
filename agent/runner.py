"""Single execution path for every tool call (agent loop, approvals, MCP): metrics, tracing, audit, error typing."""
import time

from .observability import TOOL_CALLS, TOOL_LATENCY, get_logger, tracer
from .services import ExternalServiceError


def err(kind, message, retryable):
    return {"ok": False, "error": {"type": kind, "message": message, "retryable": retryable}}


class ToolRunner:
    def __init__(self, auditor):
        self.auditor = auditor

    def run(self, tool, args, ctx):
        t0 = time.time()
        with tracer.start_as_current_span(f"tool.{tool.name}") as span:
            span.set_attribute("tool.write", tool.write)
            span.set_attribute("actor", ctx.actor)
            try:
                result = {"ok": True, "data": tool.handler(args, ctx)}
            except ValueError as e:                       # business rule: retrying the same input won't help
                result = err("business_rule", str(e), False)
            except ExternalServiceError as e:             # dependency down: safe message, retryable
                result = err("transient", str(e), True)
            except Exception:                             # never leak internals to the model
                get_logger().exception("tool failed", extra={"extra_fields": {"tool": tool.name}})
                result = err("transient", "Internal error while executing the tool.", True)
        outcome = "ok" if result["ok"] else result["error"]["type"]
        TOOL_CALLS.labels(tool.name, outcome).inc()
        TOOL_LATENCY.labels(tool.name).observe(time.time() - t0)
        self.auditor.log(actor=ctx.actor, role=ctx.role, event="tool_call", tool=tool.name, args=args.model_dump(),
                         ok=result["ok"], detail=None if result["ok"] else result["error"]["message"])
        return result
