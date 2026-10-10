"""FastAPI app: screen a question, work the review queue, read a screen's audit record.

Run:  uvicorn app.main:app --env-file .env
      open http://127.0.0.1:8000
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StringConstraints

from tradecheck import audit, parse, pipeline

STATIC_DIR = Path(__file__).resolve().parent / "static"
# A required text field: surrounding spaces are stripped, and a blank one is answered with 422.
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
KEYS = ("OPENSANCTIONS_API_KEY", "OPENROUTER_API_KEY", "TAVILY_API_KEY")
log = logging.getLogger("uvicorn.error")


def load_env(path: Path = ENV_FILE) -> list[str]:
    """Read the API keys from the project's .env, so `uvicorn app.main:app` works without
    `--env-file`. A variable that is already set wins. Returns the keys that are still
    unset (names only, never values), so startup can say what will not work."""
    try:
        from dotenv import load_dotenv  # comes with uvicorn[standard]
    except ImportError:
        pass
    else:
        load_dotenv(path, override=False)
    return [key for key in KEYS if not os.environ.get(key)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = load_env()  # at startup, not import, so importing this module never changes the environment
    if missing:
        log.warning("Not set: %s. Screens that need them fall back to saved results or Unknown. "
                    "Copy .env.example to .env and fill them in, then restart.", ", ".join(missing))
    pipeline.start()  # once per process: audit tables, the UN list, the data date
    yield


app = FastAPI(title="TradeCheck", lifespan=lifespan,
              description="Sanctions screening with the source and date behind every answer.")


class ScreenRequest(BaseModel):
    question: Text


class ReviewRequest(BaseModel):
    decision: Literal["confirm", "dismiss"]
    note: Text
    reviewer: Text


@app.get("/health")
def health() -> dict:
    """Liveness check. Touches no outside service."""
    return {"status": "ok"}


@app.post("/screen")
def screen_endpoint(req: ScreenRequest) -> dict:
    """Screen one free-text question through every layer. 422 when it names no party."""
    try:
        return pipeline.screen(req.question)
    except parse.NoParty as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@app.get("/queue")
def queue_endpoint() -> list[dict]:
    """The review cases still waiting for an analyst, oldest first."""
    return audit.queue()


@app.post("/review/{screen_id}")
def review_endpoint(screen_id: str, req: ReviewRequest) -> dict:
    """Record an analyst's decision on a review case. 404 for an unknown screen, 400 when
    it isn't a review case, 409 when it has already been reviewed."""
    try:
        return audit.review(screen_id, req.decision, req.note, req.reviewer)
    except audit.NotFound as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except audit.NotInReview as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    except audit.AlreadyReviewed as err:
        raise HTTPException(status_code=409, detail=str(err)) from err


@app.get("/audit/{screen_id}")
def audit_endpoint(screen_id: str) -> list[dict]:
    """Every audit event for one screen, oldest first."""
    events = audit.record(screen_id)
    if not events:
        raise HTTPException(status_code=404, detail=f"No screen with ID {screen_id}")
    return events


@app.get("/")
def index() -> FileResponse:
    """The one-page UI."""
    return FileResponse(STATIC_DIR / "index.html")


# Serve the static assets (index.html and anything added later).
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
