#!/usr/bin/env bash
set -uo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "$script_dir/.." && pwd)"
cd "$project_dir"

mkdir -p input output

success=0
failed=0

while IFS= read -r -d '' input_path; do
    file_name="$(basename -- "$input_path")"
    stem="${file_name%.*}"
    output_name="${stem}.json"

    printf 'Analyzing: %s\n' "$file_name"
    if docker compose run --rm analyzer \
        "/data/input/$file_name" \
        "/data/output/$output_name" </dev/null >/dev/null; then
        printf '  OK -> output/%s\n' "$output_name"
        success=$((success + 1))
    else
        printf '  FAILED\n' >&2
        failed=$((failed + 1))
    fi
done < <(
    find input -maxdepth 1 -type f \
        \( -iname '*.stp' -o -iname '*.step' \) \
        -print0 | sort -z
)

printf '\nCompleted: %d succeeded, %d failed\n' "$success" "$failed"

if command -v python >/dev/null 2>&1; then
    python scripts/summarize-results.py output
else
    printf 'Python not found; JSON files were created but CSV summary was skipped.\n'
fi

if (( failed > 0 )); then
    exit 1
fi
