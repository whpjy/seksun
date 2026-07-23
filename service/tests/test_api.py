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
