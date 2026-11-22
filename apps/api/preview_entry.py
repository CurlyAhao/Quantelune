"""Single-instance source preview API entry; never use reload/workers here."""

import argparse
import asyncio
import json
import sys
from pathlib import Path


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from preview_bootstrap import configure

    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", required=True)
    parser.add_argument("--api-port", type=int, required=True)
    parser.add_argument("--web-port", type=int, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    state = configure(root, args.instance, args.api_port, args.web_port)
    # -I ignores ambient import paths; only this immutable tree is admitted.
    sys.path.insert(0, str(root / "apps" / "api"))
    from app.config import settings
    from app.database import async_session_factory, engine, initialize_database
    from app.main import app
    from app.preview.recovery import mark_interrupted
    from app.preview.workspace import mark_workspace_interrupted

    async def recover() -> None:
        await initialize_database()
        async with async_session_factory() as db:
            count = await mark_interrupted(db)
            workspace_count = await mark_workspace_interrupted(db)
        await engine.dispose()
        (state / "recovery.json").write_text(
            json.dumps(
                {
                    "interrupted_turns": count,
                    "interrupted_plans": workspace_count,
                    "pending_replayed": 0,
                }
            ),
            encoding="utf-8",
        )

    asyncio.run(recover())

    @app.get("/api/v1/preview/runtime")
    async def runtime() -> dict:
        return {
            "instance": args.instance,
            "source_preview": True,
            "api_url": f"http://127.0.0.1:{args.api_port}/api/v1",
            "web_url": f"http://127.0.0.1:{args.web_port}",
            "storage_backend": settings.storage_backend,
            "database_backend": settings.db_backend,
        }

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=args.api_port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
