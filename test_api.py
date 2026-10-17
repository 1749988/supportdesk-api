"""Contract tests using a real temporary SQLite database, never production data."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from api import create_app
from models import Status, StatusChange, TicketCreate
from repository import Conflict, Repository

KEY = "test-only-key-not-a-real-secret"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(str(tmp_path / "tickets.sqlite3"), KEY)) as client:
        client.headers["X-API-Key"] = KEY
        yield client


def create(client, title="Printer is offline"):
    result = client.post("/api/v1/tickets", json={"title": title})
    assert result.status_code == 201
    assert result.headers["location"].endswith(str(result.json()["id"]))
    return result.json()


def test_health_and_openapi(client):
    client.headers.clear()
    assert client.get("/health").json() == {"status": "ok"}
    schema = client.get("/openapi.json").json()
    assert "APIKeyHeader" in schema["components"]["securitySchemes"]


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
def test_authentication_required(client, headers):
    client.headers.clear()
    for method, path, data in [
        ("GET", "/api/v1/tickets", None),
        ("GET", "/api/v1/tickets/1", None),
        ("GET", "/api/v1/tickets/1/events", None),
        ("GET", "/api/v1/stats", None),
        ("POST", "/api/v1/tickets", {"title": "x"}),
        ("PATCH", "/api/v1/tickets/1/status", {"status": "in_progress", "expected_version": 1}),
    ]:
        assert client.request(method, path, headers=headers, json=data).status_code == 401


@pytest.mark.parametrize(
    "data",
    [
        {"title": " "},
        {"title": "x" * 121},
        {"title": "x", "priority": "urgent"},
        {"title": "x", "unknown": True},
        {"title": "x", "description": "x" * 4001},
    ],
)
def test_invalid_ticket_rejected(client, data):
    assert client.post("/api/v1/tickets", json=data).status_code == 422
    assert client.get("/api/v1/stats").json()["total"] == 0


def test_create_and_read_preserve_literal_sql(client):
    ticket = create(client, "'); DROP TABLE tickets; --")
    assert client.get(f"/api/v1/tickets/{ticket['id']}").json() == ticket
    assert client.get("/api/v1/stats").json()["total"] == 1


def test_workflow_and_audit(client):
    ticket = create(client)
    url = f"/api/v1/tickets/{ticket['id']}"
    for version, status in enumerate(
        ["in_progress", "resolved", "in_progress", "open", "cancelled"], 1
    ):
        result = client.patch(url + "/status", json={"status": status, "expected_version": version})
        assert result.status_code == 200
        assert result.json()["version"] == version + 1
    events = client.get(url + "/events").json()
    assert [item["new_status"] for item in events] == [
        "open",
        "in_progress",
        "resolved",
        "in_progress",
        "open",
        "cancelled",
    ]
    assert events[0]["old_status"] is None
    assert events[-1]["old_status"] == "open"


def test_invalid_transition_does_not_write(client):
    ticket = create(client)
    url = f"/api/v1/tickets/{ticket['id']}"
    result = client.patch(url + "/status", json={"status": "resolved", "expected_version": 1})
    assert result.status_code == 409
    assert client.get(url).json() == ticket
    assert len(client.get(url + "/events").json()) == 1


def test_stale_version_is_rejected(client):
    ticket = create(client)
    url = f"/api/v1/tickets/{ticket['id']}/status"
    assert (
        client.patch(url, json={"status": "in_progress", "expected_version": 1}).status_code == 200
    )
    assert client.patch(url, json={"status": "resolved", "expected_version": 1}).status_code == 409


def test_filter_pagination_and_counts(client):
    first = create(client, "First")
    second = create(client, "Second")
    create(client, "Third")
    client.patch(
        f"/api/v1/tickets/{first['id']}/status", json={"status": "cancelled", "expected_version": 1}
    )
    page = client.get("/api/v1/tickets?status=open&limit=1&offset=1").json()
    assert page["total"] == 2
    assert page["items"][0]["id"] == second["id"]
    assert client.get("/api/v1/stats").json() == {
        "total": 3,
        "by_status": {"open": 2, "in_progress": 0, "resolved": 0, "cancelled": 1},
    }


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "status=unknown"])
def test_invalid_query(client, query):
    assert client.get("/api/v1/tickets?" + query).status_code == 422


def test_missing_ticket(client):
    for suffix in ["", "/events"]:
        assert client.get("/api/v1/tickets/999" + suffix).status_code == 404
    assert (
        client.patch(
            "/api/v1/tickets/999/status", json={"status": "in_progress", "expected_version": 1}
        ).status_code
        == 404
    )


def test_persistence_across_app_restarts(tmp_path):
    path = str(tmp_path / "persistent.sqlite3")
    with TestClient(create_app(path, KEY)) as first:
        first.headers["X-API-Key"] = KEY
        ticket = create(first)
    with TestClient(create_app(path, KEY)) as second:
        assert (
            second.get(f"/api/v1/tickets/{ticket['id']}", headers={"X-API-Key": KEY}).json()
            == ticket
        )


def test_two_writers_cannot_overwrite_each_other(tmp_path):
    repo = Repository(str(tmp_path / "concurrent.sqlite3"))
    repo.initialize()
    ticket = repo.create(TicketCreate(title="Concurrent change"))

    def update(status):
        try:
            repo.change_status(ticket["id"], StatusChange(status=status, expected_version=1))
            return "updated"
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(update, [Status.IN_PROGRESS, Status.CANCELLED]))
    assert sorted(results) == ["conflict", "updated"]
    assert repo.get(ticket["id"])["version"] == 2
    assert len(repo.events(ticket["id"])) == 2


def test_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv("SUPPORTDESK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="SUPPORTDESK_API_KEY"):
        create_app()
    with pytest.raises(ValueError, match="file-backed"):
        create_app(":memory:", KEY)


def test_audit_failure_rolls_back_status(tmp_path):
    repo = Repository(str(tmp_path / "rollback.sqlite3"))
    repo.initialize()
    ticket = repo.create(TicketCreate(title="Atomic update"))
    with repo.connection() as connection:
        connection.execute("""CREATE TRIGGER fail_audit BEFORE INSERT ON events
            BEGIN SELECT RAISE(ABORT, 'simulated audit failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        repo.change_status(
            ticket["id"], StatusChange(status=Status.IN_PROGRESS, expected_version=1)
        )
    assert repo.get(ticket["id"]) == ticket
    assert len(repo.events(ticket["id"])) == 1


@pytest.mark.parametrize("version", [0, True, "1", None])
def test_invalid_version(client, version):
    ticket = create(client)
    response = client.patch(
        f"/api/v1/tickets/{ticket['id']}/status",
        json={"status": "in_progress", "expected_version": version},
    )
    assert response.status_code == 422


def test_cancelled_ticket_is_terminal(client):
    ticket = create(client)
    url = f"/api/v1/tickets/{ticket['id']}/status"
    assert client.patch(url, json={"status": "cancelled", "expected_version": 1}).status_code == 200
    assert client.patch(url, json={"status": "open", "expected_version": 2}).status_code == 409
