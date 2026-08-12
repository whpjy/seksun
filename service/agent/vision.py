from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium


MANIFEST_NAME = "agent_visuals.json"


def rasterize_svg(source: Path, target: Path, width: int = 1400) -> bool:
    try:
        import cairosvg
    except (ImportError, OSError):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    cairosvg.svg2png(url=str(source), write_to=str(target), output_width=width)
    return True


def prepare_visual_observations(directory: Path, pdf_path: Path) -> list[dict[str, Any]]:
    """Rasterize bounded drawing/CAD observations for a multimodal model."""
    output = directory / "agent_visuals"
    output.mkdir(exist_ok=True)
    artifacts: list[dict[str, Any]] = []

    document = pdfium.PdfDocument(pdf_path)
    try:
        if len(document) > 0:
            page = document[0]
            bitmap = page.render(scale=2.0)
            image = bitmap.to_pil()
            image.thumbnail((1800, 1800))
            target = output / "drawing_page_1.png"
            image.save(target, format="PNG", optimize=True)
            artifacts.append(
                {
                    "id": "drawing_page_1",
                    "label": "2D PDF drawing, page 1",
                    "kind": "drawing",
                    "path": str(target.relative_to(directory)).replace("\\", "/"),
                }
            )
    finally:
        document.close()

    views = directory / "views"
    for view_id in ("front", "top", "right"):
        source = views / f"{view_id}.svg"
        if not source.is_file():
            continue
        target = output / f"cad_{view_id}.png"
        if rasterize_svg(source, target):
            artifacts.append(
                {
                    "id": f"cad_{view_id}",
                    "label": f"OCCT exact hidden-line projection: {view_id}",
                    "kind": "cad_projection",
                    "view_id": view_id,
                    "path": str(target.relative_to(directory)).replace("\\", "/"),
                }
            )

    (directory / MANIFEST_NAME).write_text(
        json.dumps({"artifacts": artifacts}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return artifacts


def multimodal_user_content(directory: Path, instruction: str) -> list[dict[str, Any]]:
    manifest = directory / MANIFEST_NAME
    content: list[dict[str, Any]] = [{"type": "text", "text": instruction}]
    if not manifest.is_file():
        return content
    data = json.loads(manifest.read_text(encoding="utf-8"))
    for artifact in data.get("artifacts", []):
        path = directory / str(artifact["path"])
        if not path.is_file():
            continue
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append(
            {
                "type": "text",
                "text": f"Observation {artifact['id']}: {artifact['label']}",
            }
        )
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded}"},
            }
        )
    return content


def artifact_image_content(
    directory: Path,
    artifacts: list[dict[str, Any]],
    instruction: str,
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": instruction}]
    for artifact in artifacts:
        path = directory / str(artifact["path"])
        if not path.is_file():
            continue
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content.extend(
            [
                {"type": "text", "text": str(artifact.get("label", artifact["path"]))},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded}"},
                },
            ]
        )
    return content
