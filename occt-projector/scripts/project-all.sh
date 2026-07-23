#!/usr/bin/env bash
set -uo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
input_root="${project_root}/../occt-analyzer/input"
analysis_root="${project_root}/../occt-analyzer/output"
output_root="${project_root}/output"
failure_file="${output_root}/batch_failures.tsv"

mkdir -p "${output_root}"
: > "${failure_file}"

succeeded=0
failed=0

while IFS= read -r -d '' step_path; do
    filename="$(basename "${step_path}")"
    stem="${filename%.*}"
    analysis_path="${analysis_root}/${stem}.json"
    model_output="${output_root}/${stem}"

    echo "Projecting: ${filename}"

    if [[ ! -f "${analysis_path}" ]]; then
        echo "  FAILED: analyzer JSON not found: ${analysis_path}"
        printf '%s\t%s\n' "${filename}" "analyzer JSON not found" >> "${failure_file}"
        failed=$((failed + 1))
        continue
    fi

    mkdir -p "${model_output}"
    chown 10001:10001 "${model_output}" 2>/dev/null || true

    if docker compose -f "${project_root}/compose.yaml" run --rm -T projector \
        "/data/input/${filename}" \
        "/data/output/${stem}" \
        "/data/analysis/${stem}.json" </dev/null; then
        echo "  OK -> output/${stem}/three_views.svg"
        succeeded=$((succeeded + 1))
    else
        echo "  FAILED: projector returned an error"
        printf '%s\t%s\n' "${filename}" "projector returned an error" >> "${failure_file}"
        failed=$((failed + 1))
    fi
done < <(
    find "${input_root}" -maxdepth 1 -type f \
        \( -iname '*.stp' -o -iname '*.step' \) -print0 | sort -z
)

python3 - "${output_root}" "${failure_file}" <<'PY'
import csv
import json
import pathlib
import sys

output_root = pathlib.Path(sys.argv[1])
failure_file = pathlib.Path(sys.argv[2])
failures = {}
if failure_file.exists():
    for line in failure_file.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        name, reason = line.split("\t", 1)
        failures[name] = reason

rows = []
for manifest_path in sorted(output_root.glob("*/views.json")):
    with manifest_path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    analysis = data.get("analysis") or {}
    views = {item["id"]: item for item in data.get("views", [])}
    front = views.get("front", {})
    top = views.get("top", {})
    right = views.get("right", {})
    rows.append({
        "model": data.get("source_file", manifest_path.parent.name),
        "status": "OK",
        "projector_schema": data.get("schema_version", ""),
        "analyzer_schema": analysis.get("schema_version", ""),
        "primary_view": data.get("primary_view", ""),
        "primary_area": next(
            (item.get("projected_area", 0) for item in data.get("views", [])
             if item.get("primary")),
            0,
        ),
        "bodies_or_groups": analysis.get("hole_axis_groups", 0),
        "hole_patterns": analysis.get("hole_patterns", 0),
        "datum_dimensions": analysis.get("datum_dimensions", 0),
        "stud_features": analysis.get("stud_features", 0),
        "front_visible": front.get("visible_edges", 0),
        "front_hidden": front.get("hidden_edges", 0),
        "front_annotations": (
            front.get("overall_dimensions", 0)
            + front.get("hole_location_dimensions", 0)
            + front.get("hole_callouts", 0)
            + front.get("radius_callouts", 0)
            + front.get("opening_dimensions", 0)
        ),
        "top_visible": top.get("visible_edges", 0),
        "right_visible": right.get("visible_edges", 0),
        "right_notes": right.get("engineering_notes", 0),
        "error": "",
    })

for model, reason in sorted(failures.items()):
    rows.append({"model": model, "status": "FAILED", "error": reason})

columns = [
    "model", "status", "projector_schema", "analyzer_schema",
    "primary_view", "primary_area",
    "bodies_or_groups", "hole_patterns", "datum_dimensions",
    "stud_features", "front_visible", "front_hidden",
    "front_annotations", "top_visible", "right_visible",
    "right_notes", "error",
]
summary_path = output_root / "batch_summary.csv"
with summary_path.open("w", newline="", encoding="utf-8-sig") as stream:
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)

print(f"Summary: {summary_path} ({len(rows)} models)")
PY

rm -f "${failure_file}"

echo
echo "Completed: ${succeeded} succeeded, ${failed} failed"
exit $((failed > 0))
