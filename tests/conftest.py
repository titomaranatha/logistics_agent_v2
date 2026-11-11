import os
from types import SimpleNamespace as NS

import fakeredis
import pytest
from fastapi.testclient import TestClient

from agent.agent import LogisticsAgent
from agent.api import create_app
from agent.config import Settings
from agent.db import Base, make_engine
from agent.security import Principal, issue_token
from agent.sessions import SessionStore

KB_DIR = os.path.join(os.path.dirname(__file__), "..", "knowledge")
SECRET = "test-secret-" + "x" * 24


def text(t):
    return NS(type="text", text=t)


def call(id_, name, **inp):
    return NS(type="tool_use", id=id_, name=name, input=inp)


class Script:
    """Scripted stand-in for the model: push steps (content blocks or an Exception to raise)."""
    def __init__(self):
        self.steps, self.calls = [], []

    def push(self, *blocks):
        self.steps.append(list(blocks)); return self

    def push_error(self, exc):
        self.steps.append(exc); return self

    def __call__(self, messages, tools):
        self.calls.append({"n_messages": len(messages), "tools": [t["name"] for t in tools]})
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return NS(content=step)


@pytest.fixture(scope="session")
def db_url(tmp_path_factory):
    if os.getenv("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]
    if os.getenv("TEST_DB") == "postgres":
        import pgserver
        srv = pgserver.get_server(tmp_path_factory.mktemp("pg"))
        pytest._pg = srv  # keep alive
        return srv.get_uri().replace("postgresql://", "postgresql+psycopg://", 1)
    return f"sqlite:///{tmp_path_factory.mktemp('db')}/test.db"


@pytest.fixture
def is_postgres(db_url):
    return db_url.startswith("postgresql")


@pytest.fixture
def build_env(db_url):
    def _build(**overrides):
        kw = dict(database_url=db_url, jwt_secret=SECRET, knowledge_dir=KB_DIR, seed_demo=True,
                  rate_limit_per_min=10_000, chat_rate_limit_per_min=10_000, llm_backoff_base=0.0)
        kw.update(overrides)
        s = Settings(**kw)
        eng = make_engine(db_url)
        Base.metadata.drop_all(eng); eng.dispose()
        redis, script = fakeredis.FakeRedis(decode_responses=True), Script()
        app = create_app(s, llm=script, redis=redis)
        svc = app.state.services
        env = NS(settings=s, app=app, client=TestClient(app), svc=svc, redis=redis, script=script,
                 store=SessionStore(redis, s.session_ttl),
                 agent=LogisticsAgent(svc.tools, svc.runner, svc.auditor, s, llm=script, sleep=lambda _: None),
                 tok=lambda role, sub=None: issue_token(SECRET, sub or f"{role}-1", role),
                 hdr=lambda role, sub=None: {"Authorization": "Bearer " + issue_token(SECRET, sub or f"{role}-1", role)},
                 principal=lambda role, sub=None: Principal(sub or f"{role}-1", role))
        return env
    return _build


@pytest.fixture
def env(build_env):
    return build_env()
