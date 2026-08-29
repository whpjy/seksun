from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, AsyncIterator

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from service.agent.events import AgentRunStore, TERMINAL_STATUSES
from service.agent.runtime import run_model_review
from service.agent.vision import prepare_visual_observations
from service.comparison import compare_c10_from_analysis
from service.correspondence import build_manufacturing_specification
from service.pdf_extraction import discover_drawing_requirements, extract_c10_from_pdf


load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

APP_VERSION = "1.0.0"
STORAGE_ROOT = Path(
    os.getenv("MEAS_STORAGE_ROOT", str(Path.home() / ".seksun-meas" / "jobs"))
).resolve()
ANALYZER_BIN = os.getenv("MEAS_ANALYZER_BIN", "occt-analyzer")
PROJECTOR_BIN = os.getenv("MEAS_PROJECTOR_BIN", "occt-projector")
MAX_UPLOAD_BYTES = int(os.getenv("MEAS_MAX_UPLOAD_MB", "200")) * 1024 * 1024
PROCESS_TIMEOUT = int(os.getenv("MEAS_PROCESS_TIMEOUT_SECONDS", "600"))
MAX_CONCURRENT_JOBS = max(1, int(os.getenv("MEAS_MAX_CONCURRENT_JOBS", "2")))
CHUNK_SIZE = 1024 * 1024
ALLOWED_RESULTS = {
    "analysis.json",
    "comparison.json",
    "measurement_plan.json",
    "vector_extraction.json",
    "pdf_extraction_diagnostics.json",
    "front.svg",
    "top.svg",
    "right.svg",
    "three_views.svg",
    "views.json",
    "agent_run.json",
    "agent_events.jsonl",
    "model_io.json",
    "manufacturing_specification.json",
    "model.stl",
}
ROOT_RESULTS = {
    "analysis.json",
    "comparison.json",
    "measurement_plan.json",
    "vector_extraction.json",
    "pdf_extraction_diagnostics.json",
    "agent_run.json",
    "agent_events.jsonl",
    "model_io.json",
    "manufacturing_specification.json",
    "model.stl",
}

job_slots = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
agent_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    initialize_history_index()
    reindex_existing_comparisons()
    yield


class ResultLinks(BaseModel):
    analysis: str
    front: str
    top: str
    right: str
    three_views: str
    manifest: str
    model: str
    manufacturing_specification: str
    archive: str


class JobResponse(BaseModel):
    id: str
    status: str
    filename: str
    size: int
    elapsed_seconds: float
    results: ResultLinks


class ComparisonLinks(ResultLinks):
    comparison: str
    measurement_plan: str
    vector_extraction: str
    extraction_diagnostics: str


class ComparisonJobResponse(BaseModel):
    id: str
    status: str
    pdf_filename: str
    step_filename: str
    pdf_size: int = 0
    step_size: int = 0
    created_at: str | None = None
    elapsed_seconds: float
    comparison: dict
    results: ComparisonLinks


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
        for item in os.getenv(
            "MEAS_CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
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
            model=file_url(job_id, "model.stl"),
            manufacturing_specification=file_url(job_id, "manufacturing_specification.json"),
            archive=f"/api/v1/jobs/{job_id}/download",
        ),
    )


def build_comparison_response(metadata: dict, comparison: dict) -> ComparisonJobResponse:
    job_id = metadata["id"]
    return ComparisonJobResponse(
        **metadata,
        comparison=comparison,
        results=ComparisonLinks(
            analysis=file_url(job_id, "analysis.json"),
            comparison=file_url(job_id, "comparison.json"),
            measurement_plan=file_url(job_id, "measurement_plan.json"),
            vector_extraction=file_url(job_id, "vector_extraction.json"),
            extraction_diagnostics=file_url(job_id, "pdf_extraction_diagnostics.json"),
            front=file_url(job_id, "front.svg"),
            top=file_url(job_id, "top.svg"),
            right=file_url(job_id, "right.svg"),
            three_views=file_url(job_id, "three_views.svg"),
            manifest=file_url(job_id, "views.json"),
            model=file_url(job_id, "model.stl"),
            manufacturing_specification=file_url(job_id, "manufacturing_specification.json"),
            archive=f"/api/v1/jobs/{job_id}/download",
        ),
    )


def write_metadata(directory: Path, metadata: dict) -> None:
    (directory / "job.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def history_db_path() -> Path:
    configured = os.getenv("MEAS_HISTORY_DB")
    if configured:
        return Path(configured).resolve()
    return STORAGE_ROOT.parent / "history.sqlite3" if STORAGE_ROOT.name == "jobs" else STORAGE_ROOT / ".history.sqlite3"


def history_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(history_db_path(), timeout=15)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_history_index() -> None:
    database = history_db_path()
    database.parent.mkdir(parents=True, exist_ok=True)
    with history_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS comparison_history (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL,
                pdf_filename TEXT NOT NULL,
                step_filename TEXT NOT NULL,
                pdf_size INTEGER NOT NULL DEFAULT 0,
                step_size INTEGER NOT NULL DEFAULT 0,
                elapsed_seconds REAL NOT NULL DEFAULT 0,
                result TEXT,
                matched INTEGER NOT NULL DEFAULT 0,
                ambiguous INTEGER NOT NULL DEFAULT 0,
                unmapped INTEGER NOT NULL DEFAULT 0,
                error TEXT
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS comparison_history_created_at ON comparison_history(created_at DESC)"
        )


def comparison_status_counts(comparison: dict | None) -> tuple[int, int, int]:
    counts = {"matched": 0, "ambiguous": 0, "unmapped": 0}
    for row in (comparison or {}).get("comparison_rows", []):
        status = row.get("mapping_status")
        if status in counts:
            counts[status] += 1
    return counts["matched"], counts["ambiguous"], counts["unmapped"]


def index_comparison(metadata: dict, comparison: dict | None = None) -> None:
    initialize_history_index()
    matched, ambiguous, unmapped = comparison_status_counts(comparison)
    now = utc_now()
    created_at = str(metadata.get("created_at") or now)
    with history_connection() as connection:
        connection.execute(
            """
            INSERT INTO comparison_history (
                id, created_at, updated_at, status, pdf_filename, step_filename,
                pdf_size, step_size, elapsed_seconds, result, matched, ambiguous,
                unmapped, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                updated_at=excluded.updated_at,
                status=excluded.status,
                pdf_filename=excluded.pdf_filename,
                step_filename=excluded.step_filename,
                pdf_size=excluded.pdf_size,
                step_size=excluded.step_size,
                elapsed_seconds=excluded.elapsed_seconds,
                result=excluded.result,
                matched=excluded.matched,
                ambiguous=excluded.ambiguous,
                unmapped=excluded.unmapped,
                error=excluded.error
            """,
            (
                metadata["id"], created_at, now, metadata.get("status", "processing"),
                metadata.get("pdf_filename", ""), metadata.get("step_filename", ""),
                int(metadata.get("pdf_size", 0)), int(metadata.get("step_size", 0)),
                float(metadata.get("elapsed_seconds", 0)),
                (comparison or {}).get("result"), matched, ambiguous, unmapped,
                metadata.get("error"),
            ),
        )


def reindex_existing_comparisons() -> None:
    for metadata_path in STORAGE_ROOT.glob("*/job.json"):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not metadata.get("pdf_filename") or not metadata.get("step_filename"):
                continue
            directory = metadata_path.parent
            if not metadata.get("created_at"):
                metadata["created_at"] = datetime.fromtimestamp(
                    metadata_path.stat().st_mtime, timezone.utc
                ).isoformat()
            comparison_path = directory / "comparison.json"
            comparison = json.loads(comparison_path.read_text(encoding="utf-8")) if comparison_path.is_file() else None
            metadata.setdefault("pdf_size", (directory / metadata["pdf_filename"]).stat().st_size if (directory / metadata["pdf_filename"]).is_file() else 0)
            metadata.setdefault("step_size", (directory / metadata["step_filename"]).stat().st_size if (directory / metadata["step_filename"]).is_file() else 0)
            index_comparison(metadata, comparison)
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue


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
    model_path = directory / "model.stl"
    views_dir = directory / "views"
    views_dir.mkdir()
    run_command([ANALYZER_BIN, str(source), str(analysis_path), str(model_path)])
    run_command([PROJECTOR_BIN, str(source), str(views_dir), str(analysis_path)])


def process_pdf_step_comparison(directory: Path, pdf_path: Path, step_path: Path) -> dict:
    discovery_mode = False
    try:
        measurement_plan, vector_extraction, diagnostics = extract_c10_from_pdf(pdf_path)
    except ValueError as exc:
        discovery_mode = True
        measurement_plan, vector_extraction, diagnostics = discover_drawing_requirements(
            pdf_path, str(exc)
        )
    for name, content in (
        ("measurement_plan.json", measurement_plan),
        ("vector_extraction.json", vector_extraction),
        ("pdf_extraction_diagnostics.json", diagnostics),
    ):
        (directory / name).write_text(
            json.dumps(content, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    process_step(directory, step_path)
    step_analysis = json.loads((directory / "analysis.json").read_text(encoding="utf-8"))
    if discovery_mode:
        candidates = measurement_plan.get("requirement_candidates", [])
        comparison = {
            "schema_version": "0.2.0",
            "measurement_id": None,
            "type": "requirement_discovery",
            "result": "not_evaluated",
            "reason": "supported_measurement_template_not_found",
            "expected": None,
            "registration": None,
            "summary": {"matched": 0, "passed": 0, "failed": 0},
            "features": [],
            "discovery": {
                "status": "needs_review",
                "candidate_count": len(candidates),
                "candidates": candidates[:100],
                "message": "已提取图纸标注候选，但尚未选择可确定性映射的检验特性。",
            },
        }
    else:
        comparison = compare_c10_from_analysis(
            measurement_plan,
            vector_extraction,
            step_analysis,
        )
    specification = build_manufacturing_specification(
        measurement_plan,
        step_analysis,
        comparison,
    )
    comparison["manufacturing_specification"] = specification
    comparison["drawing_entities"] = specification["drawing_entities"]
    comparison["cad_features"] = specification["cad_features"]
    comparison["mappings"] = specification["mappings"]
    comparison["comparison_rows"] = specification["comparison_rows"]
    (directory / "manufacturing_specification.json").write_text(
        json.dumps(specification, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (directory / "comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return comparison


def agent_result_links(job_id: str) -> dict:
    return {
        "analysis": file_url(job_id, "analysis.json"),
        "comparison": file_url(job_id, "comparison.json"),
        "measurement_plan": file_url(job_id, "measurement_plan.json"),
        "vector_extraction": file_url(job_id, "vector_extraction.json"),
        "extraction_diagnostics": file_url(job_id, "pdf_extraction_diagnostics.json"),
        "front": file_url(job_id, "front.svg"),
        "top": file_url(job_id, "top.svg"),
        "right": file_url(job_id, "right.svg"),
        "three_views": file_url(job_id, "three_views.svg"),
        "manifest": file_url(job_id, "views.json"),
        "model_io": file_url(job_id, "model_io.json"),
        "model": file_url(job_id, "model.stl"),
        "manufacturing_specification": file_url(job_id, "manufacturing_specification.json"),
        "archive": f"/api/v1/jobs/{job_id}/download",
    }


async def execute_agent_run(
    directory: Path,
    pdf_path: Path,
    step_path: Path,
) -> None:
    store = AgentRunStore(directory)
    started = time.perf_counter()
    try:
        store.update_state(status="processing")
        store.emit(
            "processing.started",
            "开始提取PDF要求并分析STEP精确几何",
            data={"pdf": pdf_path.name, "step": step_path.name},
        )
        async with job_slots:
            comparison = await asyncio.to_thread(
                process_pdf_step_comparison,
                directory,
                pdf_path,
                step_path,
            )
        discovery_mode = comparison.get("result") == "not_evaluated"
        store.emit(
            "requirements.discovered" if discovery_mode else "geometry.completed",
            (
                "旧版孔径模板未命中，已转入图纸需求发现模式"
                if discovery_mode
                else "确定性2D/3D配准和公差验证完成"
            ),
            data={
                "result": comparison.get("result"),
                "summary": comparison.get("summary", {}),
                "registration": comparison.get("registration", {}),
                "discovery": comparison.get("discovery", {}),
            },
        )
        try:
            visual_artifacts = await asyncio.to_thread(
                prepare_visual_observations, directory, pdf_path
            )
            store.emit(
                "vision.prepared",
                "已生成PDF与OCCT固定视图的多模态观察图像",
                data={
                    "artifacts": [
                        {key: value for key, value in item.items() if key != "path"}
                        for item in visual_artifacts
                    ]
                },
            )
        except Exception as exc:
            store.emit(
                "vision.failed",
                "多模态观察图像生成失败，AI将仅使用结构化证据",
                data={"error": str(exc)},
            )
        store.update_state(status="reviewing", comparison=comparison)
        agent_review = await asyncio.to_thread(run_model_review, directory, store)
        elapsed = round(time.perf_counter() - started, 3)
        final_status = "needs_review" if discovery_mode else "completed"
        store.emit(
            "run.needs_review" if discovery_mode else "run.completed",
            (
                "PDF与STEP需求发现完成，等待选择检验特性"
                if discovery_mode
                else "PDF与STEP智能审核任务完成"
            ),
            data={"result": comparison.get("result"), "elapsed_seconds": elapsed},
            model=agent_review.get("model"),
        )
        store.update_state(
            status=final_status,
            elapsed_seconds=elapsed,
            comparison=comparison,
            agent=agent_review,
        )
        write_metadata(
            directory,
            {
                "id": store.read_state()["id"],
                "status": final_status,
                "filename": step_path.name,
                "pdf_filename": pdf_path.name,
                "step_filename": step_path.name,
                "elapsed_seconds": elapsed,
            },
        )
    except Exception as exc:
        elapsed = round(time.perf_counter() - started, 3)
        store.emit(
            "run.failed",
            "智能审核任务失败",
            data={"error": str(exc), "elapsed_seconds": elapsed},
        )
        store.update_state(status="failed", error=str(exc), elapsed_seconds=elapsed)


def schedule_agent_run(directory: Path, pdf_path: Path, step_path: Path) -> None:
    task = asyncio.create_task(execute_agent_run(directory, pdf_path, step_path))
    agent_tasks.add(task)
    task.add_done_callback(agent_tasks.discard)


async def save_upload(file: UploadFile, target: Path) -> int:
    size = 0
    with target.open("wb") as stream:
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
    return size


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
        size = await save_upload(file, source)

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


@app.post("/api/v1/comparisons", response_model=ComparisonJobResponse)
async def create_comparison(
    pdf: Annotated[UploadFile, File(...)],
    step: Annotated[UploadFile, File(...)],
) -> ComparisonJobResponse:
    pdf_name = safe_filename(pdf.filename)
    step_name = safe_filename(step.filename)
    if Path(pdf_name).suffix.lower() != ".pdf":
        raise HTTPException(status_code=415, detail="2D 图纸仅支持 .pdf 文件")
    if Path(step_name).suffix.lower() not in {".stp", ".step"}:
        raise HTTPException(status_code=415, detail="3D 模型仅支持 .stp 或 .step 文件")

    job_id = uuid.uuid4().hex
    directory = job_dir(job_id)
    directory.mkdir(parents=True)
    pdf_path = directory / pdf_name
    step_path = directory / step_name
    started = time.perf_counter()
    created_at = utc_now()
    pdf_size = 0
    step_size = 0
    try:
        pdf_size = await save_upload(pdf, pdf_path)
        step_size = await save_upload(step, step_path)
        processing_metadata = {
            "id": job_id,
            "status": "processing",
            "pdf_filename": pdf_name,
            "step_filename": step_name,
            "pdf_size": pdf_size,
            "step_size": step_size,
            "created_at": created_at,
            "elapsed_seconds": 0.0,
        }
        write_metadata(directory, processing_metadata)
        index_comparison(processing_metadata)
        async with job_slots:
            comparison = await asyncio.to_thread(
                process_pdf_step_comparison,
                directory,
                pdf_path,
                step_path,
            )
        metadata = {
            "id": job_id,
            "status": "completed",
            "pdf_filename": pdf_name,
            "step_filename": step_name,
            "pdf_size": pdf_size,
            "step_size": step_size,
            "created_at": created_at,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
        write_metadata(directory, metadata)
        index_comparison(metadata, comparison)
        return build_comparison_response(metadata, comparison)
    except HTTPException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except Exception as exc:
        metadata = {
            "id": job_id,
            "status": "failed",
            "pdf_filename": pdf_name,
            "step_filename": step_name,
            "pdf_size": pdf_size,
            "step_size": step_size,
            "created_at": created_at,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "error": str(exc),
        }
        write_metadata(directory, metadata)
        index_comparison(metadata)
        raise HTTPException(status_code=422, detail=f"PDF/STEP 对比失败: {exc}") from exc
    finally:
        await pdf.close()
        await step.close()


@app.get("/api/v1/comparisons")
def list_comparisons(limit: int = 50, offset: int = 0) -> dict:
    initialize_history_index()
    safe_limit = min(max(limit, 1), 200)
    safe_offset = max(offset, 0)
    with history_connection() as connection:
        total = int(connection.execute("SELECT COUNT(*) FROM comparison_history").fetchone()[0])
        rows = connection.execute(
            """
            SELECT id, created_at, updated_at, status, pdf_filename, step_filename,
                   pdf_size, step_size, elapsed_seconds, result, matched,
                   ambiguous, unmapped, error
            FROM comparison_history
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (safe_limit, safe_offset),
        ).fetchall()
    return {"items": [dict(row) for row in rows], "total": total}


@app.get("/api/v1/comparisons/{comparison_id}")
def get_comparison(comparison_id: str) -> dict:
    directory = job_dir(comparison_id)
    metadata_path = directory / "job.json"
    comparison_path = directory / "comparison.json"
    if not metadata_path.is_file():
        raise HTTPException(status_code=404, detail="历史对比不存在")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not metadata.get("pdf_filename") or not metadata.get("step_filename"):
        raise HTTPException(status_code=404, detail="该任务不是 PDF/STEP 对比")
    if metadata.get("status") != "completed" or not comparison_path.is_file():
        raise HTTPException(status_code=422, detail=metadata.get("error", "历史对比尚未完成"))
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    metadata.setdefault("created_at", datetime.fromtimestamp(metadata_path.stat().st_mtime, timezone.utc).isoformat())
    metadata.setdefault("pdf_size", (directory / metadata["pdf_filename"]).stat().st_size if (directory / metadata["pdf_filename"]).is_file() else 0)
    metadata.setdefault("step_size", (directory / metadata["step_filename"]).stat().st_size if (directory / metadata["step_filename"]).is_file() else 0)
    response = build_comparison_response(metadata, comparison).model_dump()
    response["inputs"] = {
        "pdf": f"/api/v1/comparisons/{comparison_id}/inputs/pdf",
        "step": f"/api/v1/comparisons/{comparison_id}/inputs/step",
    }
    return response


@app.get("/api/v1/comparisons/{comparison_id}/inputs/{input_kind}")
def get_comparison_input(comparison_id: str, input_kind: str) -> FileResponse:
    directory = job_dir(comparison_id).resolve()
    metadata_path = directory / "job.json"
    if not metadata_path.is_file():
        raise HTTPException(status_code=404, detail="历史对比不存在")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    key = "pdf_filename" if input_kind == "pdf" else "step_filename" if input_kind == "step" else None
    if key is None or not metadata.get(key):
        raise HTTPException(status_code=404, detail="原始文件不存在")
    target = (directory / safe_filename(metadata[key])).resolve()
    if directory not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="原始文件不存在")
    media_type = "application/pdf" if input_kind == "pdf" else "model/step"
    return FileResponse(target, media_type=media_type, filename=None)


@app.post("/api/v1/agent-runs", status_code=202)
async def create_agent_run(
    pdf: Annotated[UploadFile, File(...)],
    step: Annotated[UploadFile, File(...)],
) -> dict:
    pdf_name = safe_filename(pdf.filename)
    step_name = safe_filename(step.filename)
    if Path(pdf_name).suffix.lower() != ".pdf":
        raise HTTPException(status_code=415, detail="2D drawing must be a PDF file")
    if Path(step_name).suffix.lower() not in {".stp", ".step"}:
        raise HTTPException(status_code=415, detail="3D model must be a STEP file")

    run_id = uuid.uuid4().hex
    directory = job_dir(run_id)
    directory.mkdir(parents=True)
    pdf_path = directory / pdf_name
    step_path = directory / step_name
    store = AgentRunStore(directory)
    state = {
        "id": run_id,
        "status": "queued",
        "pdf_filename": pdf_name,
        "step_filename": step_name,
        "created_at": store.now(),
        "elapsed_seconds": 0.0,
    }
    try:
        await save_upload(pdf, pdf_path)
        await save_upload(step, step_path)
        store.write_state(state)
        store.emit(
            "run.created",
            "已创建PDF与STEP智能审核任务",
            data={"pdf": pdf_name, "step": step_name},
        )
        schedule_agent_run(directory, pdf_path, step_path)
        return {**state, "events": f"/api/v1/agent-runs/{run_id}/events"}
    except HTTPException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(status_code=422, detail=f"Unable to create agent run: {exc}") from exc
    finally:
        await pdf.close()
        await step.close()


@app.get("/api/v1/agent-runs/{run_id}")
def get_agent_run(run_id: str) -> dict:
    directory = job_dir(run_id)
    state_path = directory / "agent_run.json"
    if not state_path.is_file():
        raise HTTPException(status_code=404, detail="Agent run does not exist")
    state = AgentRunStore(directory).read_state()
    state["results"] = agent_result_links(run_id)
    state["events"] = f"/api/v1/agent-runs/{run_id}/events"
    state["event_stream"] = f"/api/v1/agent-runs/{run_id}/stream"
    return state


@app.get("/api/v1/agent-runs/{run_id}/events")
def get_agent_events(run_id: str, after: int = 0) -> dict:
    directory = job_dir(run_id)
    store = AgentRunStore(directory)
    if not store.state_path.is_file():
        raise HTTPException(status_code=404, detail="Agent run does not exist")
    return {"events": store.read_events(max(0, after))}


@app.get("/api/v1/agent-runs/{run_id}/stream")
async def stream_agent_events(run_id: str, after: int = 0) -> StreamingResponse:
    directory = job_dir(run_id)
    store = AgentRunStore(directory)
    if not store.state_path.is_file():
        raise HTTPException(status_code=404, detail="Agent run does not exist")

    async def generate() -> AsyncIterator[str]:
        cursor = max(0, after)
        while True:
            events = store.read_events(cursor)
            for event in events:
                cursor = int(event["sequence"])
                payload = json.dumps(event, ensure_ascii=False)
                yield f"id: {cursor}\nevent: agent-event\ndata: {payload}\n\n"
            state = store.read_state()
            if state.get("status") in TERMINAL_STATUSES and not events:
                yield "event: end\ndata: {}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/agent-runs/{run_id}/artifacts/{artifact_path:path}")
def get_agent_artifact(run_id: str, artifact_path: str) -> FileResponse:
    directory = job_dir(run_id).resolve()
    if not (directory / "agent_run.json").is_file():
        raise HTTPException(status_code=404, detail="Agent run does not exist")
    target = (directory / artifact_path).resolve()
    allowed_roots = [(directory / "spatial").resolve(), (directory / "agent_visuals").resolve()]
    if not any(target == root or root in target.parents for root in allowed_roots):
        raise HTTPException(status_code=404, detail="Artifact does not exist")
    if target.suffix.lower() not in {".png", ".svg", ".json"} or not target.is_file():
        raise HTTPException(status_code=404, detail="Artifact does not exist")
    media_types = {
        ".png": "image/png",
        ".svg": "image/svg+xml",
        ".json": "application/json",
    }
    return FileResponse(target, media_type=media_types[target.suffix.lower()], filename=None)


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
    path = base / name if name in ROOT_RESULTS else base / "views" / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="结果文件不存在")
    media_type = (
        "image/svg+xml"
        if path.suffix == ".svg"
        else "application/x-ndjson"
        if path.suffix == ".jsonl"
        else "model/stl"
        if path.suffix == ".stl"
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
                path = base / name if name in ROOT_RESULTS else base / "views" / name
                if path.is_file():
                    output.write(path, name)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    source_name = metadata.get("filename") or metadata.get("step_filename") or metadata.get("pdf_filename") or job_id
    download_name = f"{Path(source_name).stem}-results.zip"
    return FileResponse(
        archive,
        media_type="application/zip",
        filename=download_name,
    )
