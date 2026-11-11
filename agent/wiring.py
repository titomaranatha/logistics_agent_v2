from dataclasses import dataclass

from .config import Settings
from .db import WMS, Auditor, Base, make_engine, make_sessionmaker
from .proposals import ProposalService
from .rag import KnowledgeBase
from .runner import ToolRunner
from .services import CarrierClient
from .tools import build_tools


@dataclass
class Services:
    settings: Settings
    engine: object
    Session: object
    wms: WMS
    auditor: Auditor
    kb: KnowledgeBase
    tools: dict
    runner: ToolRunner
    proposals: ProposalService


def build_services(settings: Settings) -> Services:
    engine = make_engine(settings.database_url)
    if settings.auto_create_tables:
        Base.metadata.create_all(engine)
    Session = make_sessionmaker(engine)
    wms, auditor = WMS(Session), Auditor(Session)
    if settings.seed_demo:
        wms.seed_demo()
    kb = KnowledgeBase.load(settings.knowledge_dir)
    tools = build_tools(wms, CarrierClient(settings.carrier_base_url, settings.carrier_api_key), kb)
    runner = ToolRunner(auditor)
    return Services(settings, engine, Session, wms, auditor, kb, tools, runner,
                    ProposalService(Session, tools, runner, auditor))
