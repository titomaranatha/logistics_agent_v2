"""Structured JSON logs, Prometheus metrics, OpenTelemetry tracing."""
import json
import logging
import sys
import time
from contextvars import ContextVar

from opentelemetry import trace
from prometheus_client import Counter, Histogram

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
tracer = trace.get_tracer("logistics_agent")

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram("http_request_duration_seconds", "HTTP latency", ["route"])
TOOL_CALLS = Counter("agent_tool_calls_total", "Tool calls", ["tool", "outcome"])
TOOL_LATENCY = Histogram("agent_tool_duration_seconds", "Tool latency", ["tool"])
LLM_LATENCY = Histogram("agent_llm_duration_seconds", "LLM call latency")
LLM_ERRORS = Counter("agent_llm_errors_total", "LLM transient errors")
RECOVERY = Counter("agent_recovery_total", "Recovery actions", ["kind"])  # repair, llm_retry, escalated
APPROVALS = Counter("agent_approvals_total", "Approval decisions", ["decision"])
RATE_LIMITED = Counter("rate_limited_total", "Rejected by rate limiter", ["scope"])
AUTH_FAILURES = Counter("auth_failures_total", "Auth failures", ["reason"])
FORBIDDEN = Counter("rbac_denied_total", "RBAC denials", ["permission"])


class JsonFormatter(logging.Formatter):
    def format(self, record):
        out = {"ts": round(time.time(), 3), "level": record.levelname, "logger": record.name,
               "msg": record.getMessage(), "request_id": request_id_var.get()}
        out.update(getattr(record, "extra_fields", {}))
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def get_logger(name="logistics_agent"):
    log = logging.getLogger(name)
    if not log.handlers:
        h = logging.StreamHandler(sys.stderr)  # stderr: stdout is reserved for MCP stdio
        h.setFormatter(JsonFormatter())
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


def log_event(msg, **fields):
    get_logger().info(msg, extra={"extra_fields": fields})


def setup_tracing(endpoint: str):
    """Export spans via OTLP/HTTP when an endpoint is configured; otherwise spans are no-ops."""
    if not endpoint:
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    provider = TracerProvider(resource=Resource.create({"service.name": "logistics-agent"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
