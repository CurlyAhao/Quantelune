"""Fixed isolated worker. No app settings, network, credentials or user code import."""

import hashlib
import json
import sys
from pathlib import Path

MAX_PAGES = 50
MAX_CODEPOINTS = 262144


def extract(source: Path) -> dict:
    from pypdf import PdfReader, __version__

    if source.stat().st_size > 4 * 1024 * 1024:
        return {"status": "unavailable", "code": "PDF_SIZE_LIMIT", "pages": []}
    reader = PdfReader(source)
    if reader.is_encrypted:
        return {"status": "unavailable", "code": "PDF_ENCRYPTED", "pages": []}
    if len(reader.pages) > MAX_PAGES:
        return {"status": "unavailable", "code": "PDF_PAGE_LIMIT", "pages": []}
    pages = []
    total = 0
    for index, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        total += len(text)
        if total > MAX_CODEPOINTS:
            return {"status": "unavailable", "code": "PDF_TEXT_LIMIT", "pages": []}
        pages.append(
            {"page": index + 1, "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}
        )
    return {
        "status": "candidate",
        "extractor": f"pypdf:{__version__}:plain-v1",
        "pages": pages,
        "code": None if any(p["text"] for p in pages) else "PDF_NO_TEXT_LAYER",
    }


def main(source: str, output: str):
    try:
        result = extract(Path(source))
    except Exception:
        result = {"status": "unavailable", "code": "PDF_EXTRACTION_FAILED", "pages": []}
    Path(output).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
