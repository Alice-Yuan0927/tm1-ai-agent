"""
TM1 AI Financial Analyst backend.

Usage:
    pip install -r backend/requirements.txt
    uvicorn backend.backend:app --reload --port 8000
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .ai.retrieval.rag import init_db
from .tm1.cache import init_schema_db, sync_schema
from .tm1.cache import is_empty as _schema_is_empty
from .services.embedding_sync import trigger_embedding_sync
from .routes import analyze as analyze_routes
from .routes import assets as assets_routes
from .routes import config as config_routes
from .routes import email as email_routes
from .routes import export as export_routes
from .routes import health as health_routes
from .routes import schema as schema_routes

_log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    init_schema_db()
    if _schema_is_empty():
        try:
            sync_schema()
        except Exception as exc:
            _log.warning("Initial schema sync skipped — TM1 not reachable yet: %s", exc)
    trigger_embedding_sync()
    yield


app = FastAPI(title="TM1 AI Analyst", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_routes.router)
app.include_router(config_routes.router)
app.include_router(schema_routes.router)
app.include_router(analyze_routes.router)
app.include_router(export_routes.router)
app.include_router(email_routes.router)
app.include_router(assets_routes.router)
