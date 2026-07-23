from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel


APP_VERSION = "1.0.0"
STORAGE_ROOT = Path(os.getenv("MEAS_STORAGE_ROOT", "/data/jobs")).resolve()
ANALYZER_BIN = os.getenv("MEAS_ANALYZER_BIN", "occt-analyzer")
PROJECTOR_BIN = os.getenv("MEAS_PROJECTOR_BIN", "occt-projector")
MAX_UPLOAD_BYTES = int(os.getenv("MEAS_MAX_UPLOAD_MB", "200")) * 1024 * 1024
PROCESS_TIMEOUT = int(os.getenv("MEAS_PROCESS_TIMEOUT_SECONDS", "600"))
MAX_CONCURRENT_JOBS = max(1, int(os.getenv("MEAS_MAX_CONCURRENT_JOBS", "2")))
CHUNK_SIZE = 1024 * 1024
ALLOWED_RESULTS = {
    "analysis.json",
    "front.svg",
    "top.svg",
    "right.svg",
    "three_views.svg",
    "views.json",
}

job_slots = asyncio.Semaphore(MAX_CONCURRENT_JOBS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    yield


class ResultLinks(BaseModel):
    analysis: str
    front: str
    top: str
    right: str
    three_views: str
    manifest: str
    archive: str


class JobResponse(BaseModel):
    id: str
    status: str
    filename: str
    size: int
    elapsed_seconds: float
    results: ResultLinks


app = FastAPI(
    title="Seksun Measurement API",
    description="Analyze STEP geometry and generate orthographic SVG drawings.",
    version=APP_VERSION,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        item.strip()
        for item in os.getenv("MEAS_CORS_ORIGINS", "http://localhost:5173").split(",")
        if item.strip()
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def safe_filename(filename: str | None) -> str:
    name = Path((filename or "model.stp").replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "_", name).strip(" .")
    return name or "model.stp"


def job_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    return STORAGE_ROOT / job_id


def file_url(job_id: str, name: str) -> str:
    return f"/api/v1/jobs/{job_id}/files/{name}"


def build_response(metadata: dict) -> JobResponse:
    job_id = metadata["id"]
    return JobResponse(
        **metadata,
        results=ResultLinks(
            analysis=file_url(job_id, "analysis.json"),
            front=file_url(job_id, "front.svg"),
            top=file_url(job_id, "top.svg"),
            right=file_url(job_id, "right.svg"),
            three_views=file_url(job_id, "three_views.svg"),
            manifest=file_url(job_id, "views.json"),
            archive=f"/api/v1/jobs/{job_id}/download",
        ),
    )


def write_metadata(directory: Path, metadata: dict) -> None:
    (directory / "job.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def run_command(arguments: list[str]) -> None:
    executable = Path(arguments[0])
    if executable.parent != Path(".") and not executable.is_file():
        raise RuntimeError(f"处理程序不存在: {arguments[0]}")
    try:
        completed = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROCESS_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"处理超时（{PROCESS_TIMEOUT} 秒）") from exc
    except OSError as exc:
        raise RuntimeError(f"无法启动处理程序 {arguments[0]}（请检查 OCCT DLL 是否在 PATH）") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(detail[-2000:] or f"处理程序退出码: {completed.returncode}")


def process_step(directory: Path, source: Path) -> None:
    analysis_path = directory / "analysis.json"
    views_dir = directory / "views"
    views_dir.mkdir()
    run_command([ANALYZER_BIN, str(source), str(analysis_path)])
    run_command([PROJECTOR_BIN, str(source), str(views_dir), str(analysis_path)])


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": APP_VERSION}


@app.post("/api/v1/jobs", response_model=JobResponse)
async def create_job(file: Annotated[UploadFile, File(...)]) -> JobResponse:
    original_name = safe_filename(file.filename)
    extension = Path(original_name).suffix.lower()
    if extension not in {".stp", ".step"}:
        raise HTTPException(status_code=415, detail="仅支持 .stp 或 .step 文件")

    job_id = uuid.uuid4().hex
    directory = job_dir(job_id)
    directory.mkdir(parents=True)
    source = directory / original_name
    size = 0
    started = time.perf_counter()

    try:
        with source.open("wb") as stream:
            while chunk := await file.read(CHUNK_SIZE):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件不能超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB",
                    )
                stream.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="上传文件为空")

        async with job_slots:
            await asyncio.to_thread(process_step, directory, source)

        metadata = {
            "id": job_id,
            "status": "completed",
            "filename": original_name,
            "size": size,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
        write_metadata(directory, metadata)
        return build_response(metadata)
    except HTTPException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except Exception as exc:
        metadata = {
            "id": job_id,
            "status": "failed",
            "filename": original_name,
            "size": size,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "error": str(exc),
        }
        write_metadata(directory, metadata)
        raise HTTPException(status_code=422, detail=f"STEP 处理失败: {exc}") from exc
    finally:
        await file.close()


@app.get("/api/v1/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str) -> JobResponse:
    metadata_path = job_dir(job_id) / "job.json"
    if not metadata_path.is_file():
        raise HTTPException(status_code=404, detail="任务不存在")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("status") != "completed":
        raise HTTPException(status_code=422, detail=metadata.get("error", "任务失败"))
    return build_response(metadata)


@app.get("/api/v1/jobs/{job_id}/files/{name}")
def get_result_file(job_id: str, name: str) -> FileResponse:
    if name not in ALLOWED_RESULTS:
        raise HTTPException(status_code=404, detail="结果文件不存在")
    base = job_dir(job_id)
    path = base / name if name == "analysis.json" else base / "views" / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="结果文件不存在")
    media_type = (
        "image/svg+xml"
        if path.suffix == ".svg"
        else "application/json"
    )
    return FileResponse(path, media_type=media_type, filename=None)


@app.get("/api/v1/jobs/{job_id}/download")
def download_results(job_id: str) -> FileResponse:
    base = job_dir(job_id)
    metadata_path = base / "job.json"
    if not metadata_path.is_file():
        raise HTTPException(status_code=404, detail="任务不存在")
    archive = base / "results.zip"
    if not archive.exists():
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
            for name in sorted(ALLOWED_RESULTS):
                path = base / name if name == "analysis.json" else base / "views" / name
                if path.is_file():
                    output.write(path, name)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    download_name = f"{Path(metadata['filename']).stem}-results.zip"
    return FileResponse(
        archive,
        media_type="application/zip",
        filename=download_name,
    )
