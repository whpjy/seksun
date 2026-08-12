import json
from pathlib import Path

from fastapi.testclient import TestClient

from service import main


def fake_process(directory: Path, source: Path) -> None:
    (directory / "analysis.json").write_text(
        json.dumps({"source_file": source.name, "solid_count": 1}),
        encoding="utf-8",
    )
    views = directory / "views"
    views.mkdir()
    for name in ("front.svg", "top.svg", "right.svg", "three_views.svg"):
        (views / name).write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (views / "views.json").write_text(
        json.dumps({"source_file": source.name}), encoding="utf-8"
    )


def test_process_and_download(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STORAGE_ROOT", tmp_path)
    monkeypatch.setattr(main, "process_step", fake_process)
    with TestClient(main.app) as client:
        response = client.post(
            "/api/v1/jobs",
            files={"file": ("part.stp", b"ISO-10303-21", "application/octet-stream")},
        )
        assert response.status_code == 200
        job = response.json()
        assert job["status"] == "completed"
        assert client.get(job["results"]["front"]).headers["content-type"].startswith(
            "image/svg+xml"
        )
        archive = client.get(job["results"]["archive"])
        assert archive.status_code == 200
        assert archive.headers["content-type"] == "application/zip"


def test_rejects_wrong_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STORAGE_ROOT", tmp_path)
    with TestClient(main.app) as client:
        response = client.post(
            "/api/v1/jobs",
            files={"file": ("part.txt", b"bad", "text/plain")},
        )
    assert response.status_code == 415


def test_creates_agent_run_without_exposing_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STORAGE_ROOT", tmp_path)
    monkeypatch.setattr(main, "schedule_agent_run", lambda *args: None)
    with TestClient(main.app) as client:
        response = client.post(
            "/api/v1/agent-runs",
            files={
                "pdf": ("drawing.pdf", b"%PDF-1.7", "application/pdf"),
                "step": ("part.step", b"ISO-10303-21", "application/octet-stream"),
            },
        )
        assert response.status_code == 202
        run = response.json()
        state = client.get(f"/api/v1/agent-runs/{run['id']}")
        events = client.get(f"/api/v1/agent-runs/{run['id']}/events")

    assert state.status_code == 200
    assert state.json()["status"] == "queued"
    assert events.json()["events"][0]["type"] == "run.created"
    assert "DASHSCOPE" not in json.dumps(state.json())


def test_agent_artifacts_are_scoped_to_run_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STORAGE_ROOT", tmp_path)
    run_id = "a" * 32
    directory = tmp_path / run_id
    spatial = directory / "spatial" / "explore_01"
    spatial.mkdir(parents=True)
    (directory / "agent_run.json").write_text(
        json.dumps({"id": run_id, "status": "completed"}), encoding="utf-8"
    )
    (spatial / "explore_01.png").write_bytes(b"fake-png")
    (tmp_path / "secret.json").write_text("{}", encoding="utf-8")

    with TestClient(main.app) as client:
        artifact = client.get(
            f"/api/v1/agent-runs/{run_id}/artifacts/spatial/explore_01/explore_01.png"
        )
        traversal = client.get(
            f"/api/v1/agent-runs/{run_id}/artifacts/%2E%2E/secret.json"
        )

    assert artifact.status_code == 200
    assert artifact.headers["content-type"] == "image/png"
    assert traversal.status_code == 404
