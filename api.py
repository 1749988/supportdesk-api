"""Run: uvicorn api:create_app --factory --host 127.0.0.1"""

import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from models import Event, Stats, Status, StatusChange, Ticket, TicketCreate, TicketPage
from repository import Conflict, MissingTicket, Repository


def create_app(db_path: str | None = None, api_key: str | None = None) -> FastAPI:
    key = api_key if api_key is not None else os.environ.get("SUPPORTDESK_API_KEY", "")
    if len(key) < 16 or not key.isascii():
        raise ValueError("Set SUPPORTDESK_API_KEY to an ASCII secret of at least 16 characters.")
    path = db_path or os.environ.get("SUPPORTDESK_DB", "data/supportdesk.sqlite3")
    if path == ":memory:":
        raise ValueError("Use a file-backed database; connections are isolated per operation.")
    repository = Repository(path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        repository.initialize()
        yield

    app = FastAPI(
        title="SupportDesk API",
        version="1.0.0",
        description="Portfolio ticket workflow with SQLite transactions and optimistic concurrency.",
        lifespan=lifespan,
    )
    header = APIKeyHeader(name="X-API-Key", auto_error=False)

    def authenticate(value: Annotated[str | None, Depends(header)]):
        if value is None or not secrets.compare_digest(value.encode(), key.encode()):
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    @app.exception_handler(MissingTicket)
    async def missing_handler(request: Request, error: MissingTicket):
        return JSONResponse(status_code=404, content={"detail": "Ticket not found"})

    @app.exception_handler(Conflict)
    async def conflict_handler(request: Request, error: Conflict):
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.exception_handler(sqlite3.OperationalError)
    async def database_handler(request: Request, error: sqlite3.OperationalError):
        return JSONResponse(
            status_code=503, content={"detail": "Database unavailable; retry later"}
        )

    @app.get("/health", tags=["health"])
    def health():
        with repository.connection() as connection:
            connection.execute("SELECT 1 FROM tickets LIMIT 1")
        return {"status": "ok"}

    router = APIRouter(prefix="/api/v1", dependencies=[Depends(authenticate)])

    @router.post("/tickets", response_model=Ticket, status_code=201, tags=["tickets"])
    def create_ticket(ticket: TicketCreate, response: Response):
        result = repository.create(ticket)
        response.headers["Location"] = f"/api/v1/tickets/{result['id']}"
        return result

    @router.get("/tickets", response_model=TicketPage, tags=["tickets"])
    def list_tickets(
        status: Status | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        return repository.list_tickets(status, limit, offset)

    @router.get("/tickets/{ticket_id}", response_model=Ticket, tags=["tickets"])
    def get_ticket(ticket_id: int):
        return repository.get(ticket_id)

    @router.patch("/tickets/{ticket_id}/status", response_model=Ticket, tags=["tickets"])
    def change_status(ticket_id: int, change: StatusChange):
        return repository.change_status(ticket_id, change)

    @router.get("/tickets/{ticket_id}/events", response_model=list[Event], tags=["tickets"])
    def ticket_events(ticket_id: int):
        return repository.events(ticket_id)

    @router.get("/stats", response_model=Stats, tags=["reporting"])
    def stats():
        return repository.stats()

    app.include_router(router)
    return app
