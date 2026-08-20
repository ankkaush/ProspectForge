"""FastAPI application - the API surface for triggering and inspecting runs.

Deliberately thin: every endpoint here does argument-handling and HTTP
concerns only, then hands off to app.trigger.start_run (or a plain query)
for anything that's actually pipeline logic. That's the same
port/adapter-style discipline as the rest of the project, applied to the
API layer - the pipeline shouldn't know or care that HTTP is one of the
ways it gets invoked.
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.logging import configure_logging
from infra.observability import init_observability
from app.orm import RunORM
from app.security import require_api_key
from app.trigger import start_run

configure_logging(level=get_settings().log_level)
init_observability()


def docs_urls_for_environment(environment: str) -> dict:
    """No endpoint here returns data without the API key (see
    app/security.py) - the interactive docs UI itself doesn't leak
    anything sensitive. Disabled in production anyway on general
    principle (Step 23's deployment review): this is an internal ops
    tool, not a public API meant to be browsed. A plain function, not
    inlined into the FastAPI(...) call, so this decision is testable
    without constructing a whole second app instance."""

    if environment == "production":
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}


app = FastAPI(
    title="ProspectForge",
    version="0.1.0",
    **docs_urls_for_environment(get_settings().environment),
)


class StartRunRequest(BaseModel):
    icp_config_id: str


class RunResponse(BaseModel):
    id: uuid.UUID
    icp_config_id: str
    status: str
    started_at: str
    completed_at: Optional[str] = None
    summary: dict

    @classmethod
    def from_orm_run(cls, run: RunORM) -> "RunResponse":
        return cls(
            id=run.id,
            icp_config_id=run.icp_config_id,
            status=run.status.value if hasattr(run.status, "value") else run.status,
            started_at=run.started_at.isoformat(),
            completed_at=run.completed_at.isoformat() if run.completed_at else None,
            summary=run.summary,
        )


@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    """Confirms the app is up AND the database is reachable - a health
    check that only proves the process is alive (without a DB round trip)
    would miss the most common real failure mode: the app boots fine, but
    the database it depends on is down or misconfigured."""

    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.post("/runs", response_model=RunResponse, dependencies=[Depends(require_api_key)])
def create_run(payload: StartRunRequest, db: Session = Depends(get_db)) -> RunResponse:
    run = start_run(payload.icp_config_id, db)
    db.commit()
    return RunResponse.from_orm_run(run)


@app.get("/runs/{run_id}", response_model=RunResponse, dependencies=[Depends(require_api_key)])
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> RunResponse:
    run = db.get(RunORM, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunResponse.from_orm_run(run)
