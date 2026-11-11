"""One-shot schema job (run before the API starts; avoids create_all races across workers/replicas).
   Initial creation only — adopt Alembic for schema evolution."""
from .config import Settings
from .db import WMS, Base, make_engine, make_sessionmaker

if __name__ == "__main__":
    s = Settings.from_env()
    engine = make_engine(s.database_url)
    Base.metadata.create_all(engine)
    if s.seed_demo:
        WMS(make_sessionmaker(engine)).seed_demo()
    print("schema ready")
