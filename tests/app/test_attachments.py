"""HTTP surface of the attachments: upload / download / delete / listing.

The handlers are thin adapters over `WorkspaceManager` (unit-tested in
`tests/agent/test_workspace.py`), so these tests check what only the transport can get
wrong: the multipart contract, the status code each domain error maps to, and which verbs
wait for an idle session.

lifespan is not run here (httpx ASGITransport does not speak it), so `app.state` gets the
session registry by hand — same approach as `tests/app/test_server.py`.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from core.agent import workspace as workspace_module
from core.app.server import create_app


class FakeSession:
    """Minimal stand-in for AgentSession: these routes only look at `is_busy`."""

    def __init__(self) -> None:
        self.is_busy = False
        self.profile = "standalone"


class FakeSessionManager:
    def __init__(self, session: FakeSession) -> None:
        self.sessions = {"s1": session}

    def get_session(self, session_id: str):
        return self.sessions.get(session_id)

    def list_sessions(self) -> list[dict]:
        return []


@pytest.fixture
def client_and_zone(tmp_path):
    """Client + the fake session 's1': (client, session, attachments dir).

    The attachments directory is *not* created — it appears on the first upload, and several
    tests assert that a refused request leaves nothing behind.
    """
    app = create_app()
    session = FakeSession()
    app.state.sessions = FakeSessionManager(session)
    app.state.session_root = tmp_path
    workspace = tmp_path / "s1" / "workspace"
    workspace.mkdir(parents=True)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return client, session, workspace / "attachments"


def _upload(name: str = "report.json", content: bytes = b'{"a": 1}') -> dict:
    return {"files": {"file": (name, content, "application/json")}}


def _seed(attachments, rel: str, content: bytes = b"{}"):
    target = attachments / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


# --- upload ---------------------------------------------------------------------


async def test_upload_writes_file_and_returns_201(client_and_zone, tmp_path):
    client, _, attachments = client_and_zone
    async with client:
        r = await client.post("/sessions/s1/attachments", **_upload())

    assert r.status_code == 201
    body = r.json()
    assert body["path"] == "report.json"
    assert body["size"] == 8
    assert body["checkpoint"].startswith("c000")
    assert (attachments / "report.json").read_bytes() == b'{"a": 1}'
    assert (tmp_path / "s1" / "checkpoints" / body["checkpoint"]).is_dir()


async def test_upload_path_query_overrides_filename(client_and_zone):
    client, _, attachments = client_and_zone
    async with client:
        r = await client.post(
            "/sessions/s1/attachments",
            params={"path": "inputs/renamed.json"},
            **_upload(),
        )

    assert r.status_code == 201
    assert r.json()["path"] == "inputs/renamed.json"
    assert (attachments / "inputs" / "renamed.json").is_file()


async def test_upload_duplicate_409_then_overwrite_201(client_and_zone):
    client, _, attachments = client_and_zone
    async with client:
        first = await client.post("/sessions/s1/attachments", **_upload(content=b"first"))
        dup = await client.post("/sessions/s1/attachments", **_upload(content=b"second"))
        forced = await client.post(
            "/sessions/s1/attachments",
            params={"overwrite": True},
            **_upload(content=b"second"),
        )

    assert first.status_code == 201
    assert dup.status_code == 409
    assert "overwrite" in dup.json()["detail"]
    assert forced.status_code == 201
    assert (attachments / "report.json").read_bytes() == b"second"


async def test_upload_oversized_413(monkeypatch, client_and_zone):
    """The body is read with cap + 1, so oversize is detected without buffering it all."""
    monkeypatch.setattr(workspace_module, "MAX_UPLOAD_BYTES", 4)
    client, _, attachments = client_and_zone
    async with client:
        r = await client.post("/sessions/s1/attachments", **_upload(content=b"xxxxx"))

    assert r.status_code == 413
    assert not attachments.exists()


@pytest.mark.parametrize(
    "path",
    [
        "../escape.json",
        "../../escape.json",
        r"a\..\..\escape.json",
        "../artifacts/report.json",
        "../.runtime/messages.jsonl",
        "/etc/passwd",
        "C:/abs.json",
        ".hidden",
    ],
)
async def test_upload_bad_path_422(client_and_zone, tmp_path, path):
    client, _, attachments = client_and_zone
    async with client:
        r = await client.post("/sessions/s1/attachments", params={"path": path}, **_upload())

    assert r.status_code == 422
    assert not attachments.exists()
    assert not (tmp_path / "escape.json").exists()
    assert not (tmp_path / "s1" / "workspace" / "escape.json").exists()


async def test_upload_bad_multipart_filename_422(client_and_zone, tmp_path):
    """With no `path`, the name comes from the client's multipart part — validated the same."""
    client, _, attachments = client_and_zone
    async with client:
        r = await client.post("/sessions/s1/attachments", **_upload(name="../escape.json"))

    assert r.status_code == 422
    assert not attachments.exists()
    assert not (tmp_path / "s1" / "workspace" / "escape.json").exists()


async def test_upload_empty_path_falls_back_to_the_multipart_filename(client_and_zone):
    client, _, attachments = client_and_zone
    async with client:
        r = await client.post("/sessions/s1/attachments", params={"path": ""}, **_upload())

    assert r.status_code == 201
    assert r.json()["path"] == "report.json"
    assert (attachments / "report.json").is_file()


async def test_upload_busy_session_409(client_and_zone):
    """A write mid-turn races the turn's own writes — the client retries after the turn."""
    client, session, attachments = client_and_zone
    session.is_busy = True
    async with client:
        r = await client.post("/sessions/s1/attachments", **_upload())

    assert r.status_code == 409
    assert not attachments.exists()


async def test_upload_without_file_422(client_and_zone):
    client, _, _ = client_and_zone
    async with client:
        r = await client.post("/sessions/s1/attachments")
    assert r.status_code == 422


# --- download -------------------------------------------------------------------


async def test_download_returns_content(client_and_zone):
    client, _, attachments = client_and_zone
    _seed(attachments, "inputs/report.json", b'{"a": 1}')

    async with client:
        r = await client.get("/sessions/s1/attachments/inputs/report.json")

    assert r.status_code == 200
    assert r.content == b'{"a": 1}'
    assert "report.json" in r.headers["content-disposition"]


async def test_download_missing_404(client_and_zone):
    client, _, _ = client_and_zone
    async with client:
        r = await client.get("/sessions/s1/attachments/nope.json")
    assert r.status_code == 404


async def test_download_directory_404(client_and_zone):
    client, _, attachments = client_and_zone
    (attachments / "adir").mkdir(parents=True)
    async with client:
        r = await client.get("/sessions/s1/attachments/adir")
    assert r.status_code == 404


async def test_download_outside_attachments_422(client_and_zone, tmp_path):
    """The agent's own part of the zone is not served here — attachments only."""
    client, _, _ = client_and_zone
    (tmp_path / "s1" / "workspace" / "artifacts").mkdir(parents=True)
    (tmp_path / "s1" / "workspace" / "artifacts" / "agent.json").write_bytes(b"{}")

    async with client:
        r = await client.get("/sessions/s1/attachments/%2E%2E/artifacts/agent.json")
    assert r.status_code == 422


async def test_reads_work_while_the_session_is_busy(client_and_zone):
    """Regression guard: gating reads on `is_busy` would freeze the UI picker mid-turn."""
    client, session, attachments = client_and_zone
    _seed(attachments, "report.json")
    session.is_busy = True

    async with client:
        listing = await client.get("/sessions/s1/attachments")
        download = await client.get("/sessions/s1/attachments/report.json")

    assert listing.status_code == 200
    assert [f["path"] for f in listing.json()["files"]] == ["report.json"]
    assert download.status_code == 200


# --- listing --------------------------------------------------------------------


async def test_listing_is_flat_and_relative(client_and_zone):
    client, _, attachments = client_and_zone
    _seed(attachments, "a/nested.json", b"22")
    _seed(attachments, "top.json", b"1")

    async with client:
        r = await client.get("/sessions/s1/attachments")

    assert r.status_code == 200
    files = r.json()["files"]
    assert [f["path"] for f in files] == ["a/nested.json", "top.json"]
    assert [f["size"] for f in files] == [2, 1]
    assert all("mtime" in f for f in files)


async def test_listing_ignores_the_rest_of_the_workspace(client_and_zone, tmp_path):
    client, _, attachments = client_and_zone
    workspace = tmp_path / "s1" / "workspace"
    (workspace / "agent-made.json").write_bytes(b"{}")
    _seed(attachments, "attached.json")

    async with client:
        r = await client.get("/sessions/s1/attachments")

    assert [f["path"] for f in r.json()["files"]] == ["attached.json"]


async def test_listing_without_the_directory(client_and_zone):
    client, _, _ = client_and_zone
    async with client:
        r = await client.get("/sessions/s1/attachments")
    assert r.json() == {"files": []}


# --- delete ---------------------------------------------------------------------


async def test_delete_removes_file_and_snapshots(client_and_zone, tmp_path):
    client, _, attachments = client_and_zone
    _seed(attachments, "report.json")

    async with client:
        r = await client.delete("/sessions/s1/attachments/report.json")

    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] == "report.json"
    assert not (attachments / "report.json").exists()
    assert (tmp_path / "s1" / "checkpoints" / body["checkpoint"]).is_dir()


async def test_delete_missing_404(client_and_zone):
    client, _, _ = client_and_zone
    async with client:
        r = await client.delete("/sessions/s1/attachments/nope.json")
    assert r.status_code == 404


async def test_delete_traversal_422(client_and_zone, tmp_path):
    """`..` is sent percent-encoded — a plain `..` is collapsed by the client, not by us,
    so only the encoded form actually reaches the handler as a path segment."""
    client, _, _ = client_and_zone
    outside = tmp_path / "s1" / "workspace" / "secret.json"
    outside.write_bytes(b"{}")

    async with client:
        r = await client.delete("/sessions/s1/attachments/%2E%2E/secret.json")

    assert r.status_code == 422
    assert outside.exists()


async def test_delete_busy_session_409(client_and_zone):
    client, session, attachments = client_and_zone
    _seed(attachments, "report.json")
    session.is_busy = True

    async with client:
        r = await client.delete("/sessions/s1/attachments/report.json")

    assert r.status_code == 409
    assert (attachments / "report.json").exists()


# --- session scoping ------------------------------------------------------------


@pytest.mark.parametrize(
    "method, path",
    [
        ("post", "/sessions/nope/attachments"),
        ("get", "/sessions/nope/attachments"),
        ("get", "/sessions/nope/attachments/report.json"),
        ("delete", "/sessions/nope/attachments/report.json"),
    ],
)
async def test_unknown_session_404(client_and_zone, method, path):
    """An unknown id is refused before it is ever interpolated into a filesystem path."""
    client, _, _ = client_and_zone
    async with client:
        kwargs = _upload() if method == "post" else {}
        r = await getattr(client, method)(path, **kwargs)
    assert r.status_code == 404
