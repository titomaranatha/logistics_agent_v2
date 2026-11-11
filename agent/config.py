import os
from dataclasses import dataclass, fields

ENV_NAMES = {"model": "AGENT_MODEL"}


@dataclass
class Settings:
    model: str = "claude-sonnet-5-5"
    database_url: str = "sqlite:///./dev.db"          # prod: postgresql+psycopg://user:pw@host/db
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = ""                               # required
    jwt_issuer: str = "logistics-agent"
    max_steps: int = 8
    max_tool_failures: int = 3
    llm_retries: int = 3
    llm_backoff_base: float = 0.5
    max_repairs: int = 1
    session_ttl: int = 3600
    rate_limit_per_min: int = 120
    chat_rate_limit_per_min: int = 20
    max_message_chars: int = 2000
    knowledge_dir: str = "knowledge"
    carrier_base_url: str = ""
    carrier_api_key: str = ""
    seed_demo: bool = False
    auto_create_tables: bool = True
    otlp_endpoint: str = ""

    @classmethod
    def from_env(cls):
        kw = {}
        for f in fields(cls):
            v = os.getenv(ENV_NAMES.get(f.name, f.name.upper()))
            if v is None:
                continue
            kw[f.name] = (v.lower() in ("1", "true", "yes")) if isinstance(f.default, bool) else type(f.default)(v)
        return cls(**kw)
