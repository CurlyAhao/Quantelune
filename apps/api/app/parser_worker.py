"""Fixed parser subprocess entry; applies limits before loading parsers."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main(request_path: str) -> None:
    if os.name != "nt":
        import resource

        limit = 2 * 1024 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
        resource.setrlimit(resource.RLIMIT_FSIZE, (512 * 1024 * 1024, 512 * 1024 * 1024))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    operation, source = request["operation"], request["source"]
    try:
        if operation == "pdf":
            from app.material_extract import extract

            result = extract(Path(source))
        elif operation in {"inspect", "convert"}:
            from app.services import multimodal_import as mm

            if operation == "inspect":
                result = mm._inspect(source)
            else:
                options = request["options"]
                result = mm._convert(
                    source, mm.Mapping.model_validate(options["mapping"]), options["destination"]
                )
        elif operation in {"signal_info", "signal_preview", "signal_package"}:
            from app.services import physiocgm

            data = physiocgm._load_package(source)
            if operation == "signal_info":
                result = {"schema": data["schema"], "packaged": "windows" in data}
            else:
                signal_options = physiocgm.Options.model_validate(
                    request["options"]["signal_options"]
                )
                if operation == "signal_preview":
                    result = physiocgm.preview(data, signal_options)
                else:
                    bundle, records = physiocgm.package_data(data, signal_options)
                    result = physiocgm.stage_package(
                        bundle, records, request["options"]["destination"]
                    )
        else:
            raise ValueError("unsupported parser operation")
    except Exception as exc:
        if operation == "pdf":
            result = {"status": "unavailable", "code": "PDF_EXTRACTION_FAILED", "pages": []}
        else:
            from app.exceptions import StatForgeError

            result = {
                "error": str(exc.detail)
                if isinstance(exc, StatForgeError)
                else "无法读取 PKL，请核对文件和数据集说明。"
            }
    Path(request["result"]).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
