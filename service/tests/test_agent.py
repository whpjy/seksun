import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from service.agent.events import AgentRunStore
from service.agent.runtime import _deterministic_review, _json_object, run_model_review
from service.agent.tools import ComparisonToolRegistry
from service.agent.vision import multimodal_user_content, prepare_visual_observations


def write_artifacts(directory):
    (directory / "measurement_plan.json").write_text(
        json.dumps({"measurements": [{"id": "C10", "nominal": 6.5}]}),
        encoding="utf-8",
    )
    (directory / "vector_extraction.json").write_text(
        json.dumps({"annotation_bindings": [], "vector_features": []}),
        encoding="utf-8",
    )
    (directory / "analysis.json").write_text(
        json.dumps({"schema_version": "test", "axial_features": []}),
        encoding="utf-8",
    )
    (directory / "comparison.json").write_text(
        json.dumps(
            {
                "result": "fail",
                "summary": {"matched": 6, "passed": 5, "failed": 1},
                "features": [{"id": "H06", "result": "fail"}],
            }
        ),
        encoding="utf-8",
    )


def test_mock_agent_inspects_all_required_evidence(tmp_path, monkeypatch):
    write_artifacts(tmp_path)
    monkeypatch.setenv("AGENT_MODEL_MODE", "mock")
    monkeypatch.setattr(
        ComparisonToolRegistry,
        "render_spatial_view",
        lambda self, arguments: {
            "view_id": "explore_01",
            "direction": arguments["direction"],
            "artifacts": [],
        },
    )
    store = AgentRunStore(tmp_path)

    result = run_model_review(tmp_path, store, default_model="test-model")

    assert result["mode"] == "mock"
    assert result["missing_tools"] == []
    assert result["inspected_tools"] == [
        "inspect_cad_features",
        "inspect_comparison_evidence",
        "inspect_drawing_requirements",
        "render_spatial_view",
    ]
    events = store.read_events()
    assert [event["type"] for event in events].count("tool.completed") == 5
    assert events[-1]["type"] == "model.completed"
    model_io = json.loads(store.model_io_path.read_text(encoding="utf-8"))
    assert len(model_io) == 6
    assert "api_key" not in json.dumps(model_io).lower()
    assert "reasoning_content" not in json.dumps(model_io).lower()
    assert result["structured_review"]["findings"][0]["result"] == "fail"


def test_prepares_bounded_multimodal_observations(tmp_path):
    pdf_path = tmp_path / "drawing.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=200)
    with pdf_path.open("wb") as stream:
        writer.write(stream)
    views = tmp_path / "views"
    views.mkdir()
    svg = "<svg xmlns='http://www.w3.org/2000/svg' width='100' height='50'><circle cx='50' cy='25' r='10'/></svg>"
    for view in ("front", "top", "right"):
        (views / f"{view}.svg").write_text(svg, encoding="utf-8")

    artifacts = prepare_visual_observations(tmp_path, pdf_path)
    content = multimodal_user_content(tmp_path, "inspect")

    assert artifacts[0]["id"] == "drawing_page_1"
    images = [item for item in content if item["type"] == "image_url"]
    assert len(images) == len(artifacts)
    assert len(images) >= 1
    assert all(item["image_url"]["url"].startswith("data:image/png;base64,") for item in images)


def test_extracts_json_after_model_preface_and_markdown_fence():
    content = 'Based on the evidence:\n```json\n{"headline":"发现尺寸差异","overview":"第六孔超差"}\n```'

    parsed = _json_object(content)

    assert parsed == {"headline": "发现尺寸差异", "overview": "第六孔超差"}


def test_spatial_view_rejects_parallel_camera_axes(tmp_path):
    registry = ComparisonToolRegistry(tmp_path)

    with pytest.raises(ValueError, match="must not be parallel"):
        registry.render_spatial_view(
            {
                "direction": [1, 0, 0],
                "x_direction": [-1, 0, 0],
                "purpose": "invalid camera frame",
            }
        )


def test_spatial_view_rejects_duplicate_direction(tmp_path):
    registry = ComparisonToolRegistry(tmp_path)
    registry._spatial_directions.append([1.0, 0.0, 0.0])

    with pytest.raises(ValueError, match="duplicates"):
        registry.render_spatial_view(
            {
                "direction": [0.999, 0.01, 0],
                "x_direction": [0, 1, 0],
                "purpose": "duplicate direction",
            }
        )


def test_spatial_followup_marks_failed_cad_feature(tmp_path, monkeypatch):
    (tmp_path / "part.step").write_text("STEP", encoding="utf-8")
    (tmp_path / "analysis.json").write_text(
        json.dumps(
            {
                "hole_axis_groups": [
                    {"id": "HG006", "feature_ids": ["HF006"]}
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "comparison.json").write_text(
        json.dumps(
            {
                "result": "fail",
                "features": [
                    {
                        "pdf_feature_id": "VG_HOLE_06",
                        "step_feature_id": "HF006:1",
                        "result": "fail",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def fake_projector(command, **_):
        output = Path(command[2])
        request = json.loads((output / "request.json").read_text(encoding="utf-8"))
        view_id = request["views"][0]["id"]
        (output / f"{view_id}.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"><g data-hole-groups="HG006"><circle class="feature-target"/></g></svg>',
            encoding="utf-8",
        )
        (output / "views.json").write_text(
            json.dumps({"views": [{"id": view_id}]}), encoding="utf-8"
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_rasterize(_, target):
        target.write_bytes(b"PNG")
        return True

    monkeypatch.setattr("service.agent.tools.subprocess.run", fake_projector)
    monkeypatch.setattr("service.agent.tools.rasterize_svg", fake_rasterize)
    registry = ComparisonToolRegistry(tmp_path)
    registry.render_spatial_view(
        {
            "direction": [1, -1, 1],
            "x_direction": [1, 1, 0],
            "purpose": "overview",
        }
    )
    followup = registry.render_spatial_view(
        {
            "direction": [-1, -1, 0.4],
            "x_direction": [1, -1, 0],
            "purpose": "verify failed hole",
            "focus_feature_id": "HFO06",
            "based_on_view_id": "explore_01",
        }
    )

    assert followup["stage"] == "focused_verification"
    assert followup["focus_feature_id"] == "HG006"
    assert followup["requested_focus_feature_id"] == "HFO06"
    assert followup["focus_resolution"] == "fallback_to_failed_feature"
    marked_svg = (tmp_path / "spatial" / "explore_02" / "explore_02.svg").read_text(encoding="utf-8")
    assert 'data-agent-focus="HG006"' in marked_svg


def test_discovery_review_does_not_invent_pass_fail(tmp_path):
    (tmp_path / "comparison.json").write_text(
        json.dumps(
            {
                "result": "not_evaluated",
                "features": [],
                "discovery": {"candidate_count": 12},
            }
        ),
        encoding="utf-8",
    )

    review = _deterministic_review(tmp_path, "发现多个候选标注")

    assert review["headline"] == "图纸需求已发现，等待选择检验特性"
    assert review["findings"] == []
    assert "12" in review["drawing_requirement"]
