"""Executable entry point for the packaged StatForge FastAPI sidecar."""

from __future__ import annotations

import argparse
import os
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the StatForge local API sidecar")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    return parser.parse_args()


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--parser-worker":
        from app.parser_worker import main as parser_worker_main

        parser_worker_main(sys.argv[2])
        return
    from app.startup_diagnostics import note

    note("python_entry")
    if len(sys.argv) == 3 and sys.argv[1] == "--ml-worker":
        from app.ml.worker import main as ml_worker_main

        ml_worker_main(sys.argv[2])
        return
    args = parse_args()
    if args.host != "127.0.0.1":
        raise SystemExit("The desktop sidecar may only listen on 127.0.0.1")
    os.environ.setdefault("DESKTOP_MODE", "true")
    import uvicorn

    # Import the ASGI app directly so freezer tools can discover the complete
    # application dependency graph. A string import works in development but
    # leaves dynamically imported routers and services out of a frozen binary.
    note("import_main")
    from app.main import app

    note("main_imported")
    import logging
    import re

    class MediaAccessLog(logging.Filter):
        def filter(self, record):
            if isinstance(record.args, tuple):
                record.args = tuple(
                    re.sub(r"(/api/v1/materials/[0-9a-f-]+/playback/)[^?\s]+", r"\1[stream]", value)
                    if isinstance(value, str)
                    else value
                    for value in record.args
                )
            return True

    logging.getLogger("uvicorn.access").addFilter(MediaAccessLog())
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=os.environ.get("STATFORGE_LOG_LEVEL", "info"),
        access_log=True,
        loop="app.research.server_loop:new_loop",
    )


if __name__ == "__main__":
    main()
