"""Run an extracted figure package without StatForge or an API connection."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from datetime import datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--edited",
        action="store_true",
        help="Allow edits to figure.py; retain data and renderer checks.",
    )
    parser.add_argument(
        "--output", help="New output directory; existing directories are never overwritten."
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    changed = []
    for name, expected in manifest["files"].items():
        content = (root / name).read_bytes()
        if (
            len(content) != expected["bytes"]
            or hashlib.sha256(content).hexdigest() != expected["sha256"]
        ):
            if name == "figure.py" and args.edited:
                changed.append(name)
            else:
                raise SystemExit(f"File changed: {name}. Extract the original package again.")
    versions = {name: importlib.metadata.version(name) for name in manifest["required_versions"]}
    if versions != manifest["required_versions"]:
        raise SystemExit(
            "Library versions differ. Install requirements.txt in a separate environment first."
        )

    # Load the data through the same Polars -> Pandas path as the original worker.
    import polars as pl
    from renderer import render

    frame = pl.read_parquet(root / "data.parquet")
    if frame.columns != manifest["columns"] or frame.height != manifest["input_rows"]:
        raise SystemExit("Selected data dimensions differ from the saved run.")
    code = (root / "figure.py").read_bytes().decode("utf-8")
    result = render(code, frame.to_pandas(), manifest["source_sha256"], dpi=manifest["dpi"])
    output = (
        Path(args.output).resolve()
        if args.output
        else root / ("output-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    )
    output.mkdir(exist_ok=False)
    actual = {}
    matches = {}
    for extension, content in result.pop("files").items():
        if extension not in ("svg", "png", "pdf"):
            continue
        (output / ("figure." + extension)).write_bytes(content)
        actual[extension] = hashlib.sha256(content).hexdigest()
        matches[extension] = (
            actual[extension] == manifest["files"]["original/figure." + extension]["sha256"]
        )
    report = {
        **result,
        "renderer_origin": manifest["renderer_origin"],
        "edited_code": bool(changed),
        "software_versions": versions,
        "output_sha256": actual,
        "matches_original_images": matches,
        "all_images_match": all(matches.values()),
    }
    (output / "reproduction.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "edited_code": bool(changed),
                "matches_original_images": matches,
            },
            ensure_ascii=False,
        )
    )
    if not changed and not all(matches.values()):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
