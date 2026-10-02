import pytest
from fastapi.testclient import TestClient

from rdtg.server import create_app

from .conftest import DEMO


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RDTG_HOME", str(tmp_path / "home"))
    return TestClient(create_app())


def test_starts_with_no_workspace(client):
    assert client.get("/api/workspace").json()["open"] is False
    assert client.get("/api/project").status_code == 409
    assert client.get("/api/workspaces/recent").json() == []


def test_create_open_close_and_recent(client, tmp_path):
    new = tmp_path / "Brake"
    r = client.post("/api/workspaces/create", json={"path": str(new), "name": "Brake Controller"})
    assert r.status_code == 200 and r.json()["name"] == "Brake Controller"
    assert (new / "rdtg.yaml").exists()
    assert client.get("/api/project").json()["requirements"] == []

    # cannot create over an existing workspace
    assert client.post("/api/workspaces/create", json={"path": str(new)}).status_code == 400

    client.post("/api/workspaces/open", json={"path": str(DEMO)})
    assert client.get("/api/workspace").json()["name"] == "Engine Controller Demo"
    names = [w["name"] for w in client.get("/api/workspaces/recent").json()]
    assert names == ["Engine Controller Demo", "Brake Controller"]

    client.post("/api/workspaces/close")
    assert client.get("/api/graph").status_code == 409
    remaining = client.request("DELETE", "/api/workspaces/recent", params={"path": str(new)}).json()
    assert [w["name"] for w in remaining] == ["Engine Controller Demo"]


def test_open_rejects_non_workspace(client, tmp_path):
    assert client.post("/api/workspaces/open", json={"path": str(tmp_path)}).status_code == 400


def test_fs_list_flags_workspaces(client):
    d = client.get("/api/fs/list", params={"path": str(DEMO.parent)}).json()
    flags = {x["name"]: x["is_workspace"] for x in d["dirs"]}
    assert flags["workspace"] is True and flags["software"] is False
