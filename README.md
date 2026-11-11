# Logistics Operations Agent (v2)

Production-oriented AI agent for warehouse operations: answers stock/dock/shipment questions, retrieves SOPs,
and proposes write actions that a human approves. Built on Claude tool use, exposed over REST **and MCP**.

```
Client ──JWT──> FastAPI ──> Agent loop (Claude) ──> ToolRunner ──> PostgreSQL (WMS, audit, proposals)
 MCP client ──> MCP server ──────────┘     │             └──────> Carrier API (retry + circuit breaker)
                                           ├─ RBAC per tool · schema validation · approval gate
                                           ├─ Redis: sessions, locks, rate limits
                                           └─ RAG: SOP retrieval (BM25) · answer verification
```

## Quick start
```bash
cp .env.example .env            # set POSTGRES_PASSWORD, JWT_SECRET, ANTHROPIC_API_KEY
docker compose up --build       # postgres + redis + migrate job + api + prometheus
JWT_SECRET=... python scripts/issue_token.py alice operator     # tokens come from your IdP in real use
curl -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
     -d '{"message":"What do I do with a damaged pallet?"}' localhost:8000/chat
```
MCP (stdio): `MCP_ROLE=operator python -m agent.mcp_server` — add to Claude Desktop/any MCP client.

## What changed vs v1
| Area | Implementation |
|---|---|
| Database | PostgreSQL via SQLAlchemy; row locks prevent overselling; unique keys give idempotency; stock is actually reserved |
| Sessions | Redis, TTL, per-session distributed lock, owner-only access (404 not 403, no enumeration) |
| Reasoning | Typed errors (`business_rule` vs `transient`), LLM retry/backoff, **answer grounding check + one repair turn**, escalation after repeated failures, step cap |
| RAG | Markdown SOPs chunked by heading, BM25 with stemming; returns nothing off-topic so the model says "not documented" |
| MCP | Same governed registry; tools filtered by role; writes become durable proposals |
| Monitoring | JSON logs (request-id), Prometheus metrics, OpenTelemetry spans (OTLP), DB audit trail incl. denied calls |
| Security | JWT (HS256, exp/iss required), roles viewer/operator/supervisor/admin, least-privilege tool lists, four-eyes on proposals, per-user + stricter LLM rate limits |
| Deployment | Non-root image, health checks (`/health/live`, `/health/ready`), migration job, persistent volumes, CI with Postgres service |
| Testing | 48 tests: unit, recovery, API integration, security, MCP, concurrency, eval harness; locust load test |

## Roles
viewer: read · operator: + request writes · supervisor: + approve · admin: + audit. Operators cannot approve.

## Tests
```bash
pip install -r requirements-dev.txt
pytest -q                          # SQLite
TEST_DB=postgres pytest -q         # real Postgres (embedded) — includes the overselling race test
python -m evals.run_evals          # live-model behavioural evals (needs ANTHROPIC_API_KEY)
LOAD_TOKEN=... locust -f tests/load/locustfile.py --host http://localhost:8000
```

## Verification status (be precise)
**Run:** 48/48 tests on PostgreSQL, 47 + 1 skipped on SQLite; ruff clean; load test (stub model, 30 users, 25s: 3,137 req,
0 failures, ~125 req/s, `/chat` p95 170 ms — single worker, embedded Postgres, in-process fakeredis, shared sandbox: indicative only);
MCP stdio smoke test.
**Not run:** Docker build/compose, CI on GitHub, real Redis server, OTLP collector, live Claude calls (tests use a scripted model;
`evals/` is the live check).

## Known gaps / next
Alembic migrations (current job only creates the initial schema) · OIDC/IdP instead of script-issued JWTs · MCP over HTTP needs
its own auth (stdio uses a fixed service principal) · grounding check is numeric-only · eval set is 6 cases · conversation
history is not trimmed · embeddings/pgvector for RAG at scale · approval notifications (Slack/email) · secrets manager.


## Author
**Tito M** — AI Automation | AI Agents | Product Design | Data
