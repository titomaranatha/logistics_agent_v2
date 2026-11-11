import time
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from . import observability as obs
from .agent import LogisticsAgent
from .config import Settings
from .db import ping
from .security import AuthError, Principal, RateLimiter, decode_token
from .sessions import SessionStore
from .wiring import build_services

HEX32 = r"^[0-9a-f]{32}$"


def create_app(settings: Settings | None = None, *, llm=None, redis=None, services=None) -> FastAPI:
    s = settings or Settings.from_env()
    if not s.jwt_secret or len(s.jwt_secret) < 16:
        raise RuntimeError("JWT_SECRET must be set (>=16 chars)")
    if redis is None:
        from redis import Redis
        redis = Redis.from_url(s.redis_url, decode_responses=True)
    obs.setup_tracing(s.otlp_endpoint)
    svc = services or build_services(s)
    store, limiter = SessionStore(redis, s.session_ttl), RateLimiter(redis)
    agent = LogisticsAgent(svc.tools, svc.runner, svc.auditor, s, llm=llm)

    class ChatIn(BaseModel):
        message: str = Field(min_length=1, max_length=s.max_message_chars)
        session_id: str | None = Field(default=None, pattern=HEX32)

    class ApproveIn(BaseModel):
        session_id: str = Field(pattern=HEX32)
        decisions: dict[str, bool]

    class DecisionIn(BaseModel):
        approve: bool

    app = FastAPI(title="Logistics Operations Agent", version="2.0")

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        obs.request_id_var.set(rid)
        t0 = time.time()
        response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        obs.HTTP_REQUESTS.labels(request.method, route, response.status_code).inc()
        obs.HTTP_LATENCY.labels(route).observe(time.time() - t0)
        response.headers["x-request-id"] = rid
        if route not in ("/metrics", "/health/live"):
            obs.log_event("http_request", method=request.method, route=route, status=response.status_code,
                          ms=round((time.time() - t0) * 1000))
        return response

    # ---- auth + rate limiting ----
    def principal(authorization: str = Header(None)) -> Principal:
        if not authorization or not authorization.startswith("Bearer "):
            obs.AUTH_FAILURES.labels("missing").inc()
            raise HTTPException(401, "Missing bearer token")
        try:
            p = decode_token(s.jwt_secret, authorization[7:], s.jwt_issuer)
        except AuthError as e:
            obs.AUTH_FAILURES.labels(str(e)).inc()
            raise HTTPException(401, "Invalid or expired token") from None
        ok, retry = limiter.check("api", p.sub, s.rate_limit_per_min)
        if not ok:
            obs.RATE_LIMITED.labels("api").inc()
            raise HTTPException(429, "Rate limit exceeded", headers={"Retry-After": str(retry)})
        return p

    def llm_principal(p: Principal = Depends(principal)) -> Principal:  # stricter limit: LLM calls cost money
        ok, retry = limiter.check("llm", p.sub, s.chat_rate_limit_per_min)
        if not ok:
            obs.RATE_LIMITED.labels("llm").inc()
            raise HTTPException(429, "Chat rate limit exceeded", headers={"Retry-After": str(retry)})
        return p

    def finish(result):
        if result.get("code") == "llm_unavailable":
            raise HTTPException(503, result["message"], headers={"Retry-After": "5"})
        return result

    # ---- health / metrics ----
    @app.get("/health/live")
    def live():
        return {"ok": True}

    @app.get("/health/ready")
    def ready(response: Response):
        checks = {}
        for name, fn in (("database", lambda: ping(svc.engine)), ("redis", redis.ping)):
            try:
                fn(); checks[name] = "ok"
            except Exception:
                checks[name] = "down"
        if "down" in checks.values():
            response.status_code = 503
        return checks

    @app.get("/metrics")  # keep on the internal network / scrape-only ingress
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # ---- agent ----
    @app.post("/chat")
    def chat(body: ChatIn, p: Principal = Depends(llm_principal)):
        state = store.load(body.session_id) if body.session_id else store.create(p)
        if not state or state["user"] != p.sub:          # same 404 for missing and not-yours: no enumeration
            raise HTTPException(404, "Unknown session")
        with store.lock(state["id"]):
            state = store.load(state["id"])
            try:
                result = agent.chat(state, body.message)
            finally:
                store.save(state)
        return finish(result)

    @app.post("/approve")
    def approve(body: ApproveIn, p: Principal = Depends(llm_principal)):
        if not p.can("actions:approve"):
            obs.FORBIDDEN.labels("actions:approve").inc()
            raise HTTPException(403, "Requires approver role")
        state = store.load(body.session_id)
        if not state:
            raise HTTPException(404, "Unknown session")
        with store.lock(state["id"]):
            state = store.load(state["id"])
            try:
                result = agent.resolve(state, p, body.decisions)
            except PermissionError as e:
                raise HTTPException(403, f"Missing permission: {e}") from None
            finally:
                store.save(state)
        return finish(result)

    # ---- durable proposals (MCP / async approval) ----
    @app.get("/proposals")
    def list_proposals(status: str | None = None, p: Principal = Depends(principal)):
        return svc.proposals.list(p, status)

    @app.post("/proposals/{pid}/decision")
    def decide(pid: str, body: DecisionIn, p: Principal = Depends(principal)):
        try:
            return svc.proposals.decide(pid, body.approve, p)
        except PermissionError as e:
            obs.FORBIDDEN.labels("actions:approve").inc()
            raise HTTPException(403, str(e)) from None
        except KeyError:
            raise HTTPException(404, "Unknown proposal") from None
        except ValueError as e:
            raise HTTPException(409, str(e)) from None

    @app.get("/audit")
    def audit(limit: int = 100, p: Principal = Depends(principal)):
        if not p.can("audit:read"):
            obs.FORBIDDEN.labels("audit:read").inc()
            raise HTTPException(403, "Requires admin")
        return svc.auditor.recent(min(limit, 500))

    app.state.services = svc
    return app


def app_factory():  # uvicorn agent.api:app_factory --factory
    return create_app()
