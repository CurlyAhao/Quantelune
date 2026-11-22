"""Private, fixed local worker. Accepts data parameters, never user code."""

import json
import sys
from pathlib import Path

if __name__ == "__main__":
    print(json.dumps({"label": "正在加载本地处理组件"}, ensure_ascii=True), flush=True)
    from media_features import process_coco, process_media, process_ravdess

    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    functions = {"ravdess": process_ravdess, "coco": process_coco, "media": process_media}

    def progress(done, total, label):
        print(
            json.dumps(
                {"completed": done, "total": total, "label": f"{done}/{total} · {label}"},
                ensure_ascii=True,
            ),
            flush=True,
        )

    result = functions[request["kind"]](
        Path(request["root"]),
        request["samples"],
        Path(request["output"]),
        request["options"],
        progress,
    )
    Path(request["output"], "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
