"""App wired with a deterministic stub model, for load tests (measures our stack, not the LLM).
   DATABASE_URL/REDIS_URL honoured; --embedded-pg boots a throwaway Postgres for local runs."""
import json
import os
import sys
import uuid
from types import SimpleNamespace as NS

from agent.api import create_app
from agent.config import Settings
from agent.security import issue_token


def stub_llm(messages, tools):
    last = messages[-1]
    if last["role"] == "user" and isinstance(last["content"], list):          # tool result -> final answer
        total = json.loads(last["content"][0]["content"])["data"]["total_available"]
        return NS(content=[NS(type="text", text=f"{total} units available")])
    return NS(content=[NS(type="tool_use", id=uuid.uuid4().hex[:12], name="check_inventory", input={"sku": "SKU-1001"})])


def make_app(db_url):
    redis = None
    if not os.getenv("REDIS_URL"):
        import fakeredis

        redis = fakeredis.FakeRedis(decode_responses=True)
    s = Settings(database_url=db_url, redis_url=os.getenv("REDIS_URL", ""), jwt_secret="load-test-secret-" + "x" * 16,
                 seed_demo=True, knowledge_dir="knowledge", rate_limit_per_min=10**6, chat_rate_limit_per_min=10**6)
    return create_app(s, llm=stub_llm, redis=redis)


if __name__ == "__main__":
    import uvicorn
    url = os.getenv("DATABASE_URL", "")
    if "--embedded-pg" in sys.argv:
        import tempfile

        import pgserver
        url = pgserver.get_server(tempfile.mkdtemp()).get_uri().replace("postgresql://", "postgresql+psycopg://", 1)
    print("LOAD_TOKEN=" + issue_token("load-test-secret-" + "x" * 16, "load-user", "operator", ttl=7200), flush=True)
    uvicorn.run(make_app(url), host="127.0.0.1", port=8099, log_level="warning")
