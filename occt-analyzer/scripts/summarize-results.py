#!/usr/bin/env python3
import csv
import json
import sys
from pathlib import Path


def main() -> int:
    output_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "output")
    result_paths = sorted(output_dir.glob("*.json"))
    rows = []

    for path in result_paths:
        try:
            with path.open(encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, json.JSONDecodeError) as error:
            print(f"Skipping {path}: {error}", file=sys.stderr)
            continue

        topology = data.get("topology", {})
        measurements = data.get("measurements", {})
        bounds = measurements.get("bounding_box", {}).get("size", [None] * 3)
        thickness = data.get("thickness_analysis", {})
        radii = data.get("radius_pair_analysis", {})
        axial_features = data.get("axial_features", [])

        rows.append({
            "source_file": data.get("source_file", path.stem),
            "schema": data.get("schema_version", ""),
            "solids": topology.get("solids", 0),
            "faces": topology.get("faces", 0),
            "surface_area_mm2": measurements.get("surface_area", 0),
            "volume_mm3": measurements.get("volume", 0),
            "bbox_x_mm": bounds[0],
            "bbox_y_mm": bounds[1],
            "bbox_z_mm": bounds[2],
            "hole_layers": sum(
                1 for item in axial_features if "HOLE" in item.get("type", "")
            ),
            "hole_axis_groups": len(data.get("hole_axis_groups", [])),
            "hole_patterns": len(data.get("hole_patterns", [])),
            "dominant_thickness_mm": thickness.get("dominant_thickness", 0),
            "thickness_evidence": thickness.get("dominant_evidence", 0),
            "thickness_assessment": thickness.get("assessment", ""),
            "torus_pairs": len(radii.get("torus_pairs", [])),
            "bend_pairs": len(radii.get("bend_pairs", [])),
        })

    summary_path = output_dir / "batch_summary.csv"
    if rows:
        with summary_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    print(f"Summary: {summary_path} ({len(rows)} models)")
    for row in rows:
        print(
            f"  {row['source_file']}: "
            f"bodies={row['solids']} faces={row['faces']} "
            f"holes={row['hole_layers']}/{row['hole_axis_groups']} "
            f"thickness={row['dominant_thickness_mm']} "
            f"({row['thickness_assessment']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
