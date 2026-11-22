"""Agent access to the same image comparison service used by the workspace."""

import asyncio
import uuid

from app.database import async_session_factory
from app.models.user import User
from app.services import visual_samples


def visual_tools(live):
    from app.research.project_chat_tools import attach_result, json_text

    async def compare_project_images(
        dataset_ids: list[str], descriptions: dict[str, str] | None = None, force: bool = False
    ) -> str:
        """Extract ResNet18 vectors for selected real project images and save their
        summary. Supply actual dataset IDs, optional user-provided descriptions and
        force only when re-execution is requested. Does not inspect image content
        through the language model.
        """
        task = (live.value.get("task_context") or {}).get("id")
        async with async_session_factory() as db:
            user = await db.get(User, live.owner)
            body = visual_samples.VisualRequest(
                dataset_ids=[uuid.UUID(item) for item in dataset_ids],
                descriptions=descriptions or {},
                task_id=uuid.UUID(task) if task else None,
                force=force,
            )
            run = await visual_samples.start(db, user, live.project, body)
        reused = run.get("reused", False)
        while run["status"] in {"queued", "running"}:
            live.check()
            live.progress(run["progress"] or "正在提取图像特征…")
            await asyncio.sleep(1)
            async with async_session_factory() as db:
                user = await db.get(User, live.owner)
                row, value = await visual_samples.owned_run(
                    db, user, live.project, uuid.UUID(run["id"])
                )
                run = visual_samples.public(row, value)
        if run["status"] != "completed":
            return json_text(
                {"status": run["status"], "reason": run.get("error_message"), "run_id": run["id"]}
            )
        live.value["dataset_id"] = run["summary_id"]
        return json_text(
            {
                "status": "completed",
                "run_id": run["id"],
                "reused": reused,
                "summary": attach_result(
                    live, "dataset", run["summary_id"], "图像比较摘要", run["summary_id"]
                ),
                "vectors": attach_result(
                    live, "dataset", run["package_id"], "图像完整特征", run["package_id"]
                ),
                "method": run["parameters"].get("method"),
            }
        )

    async def read_image_similarity(run_id: str, reference_dataset_id: str) -> str:
        """Read real cosine similarities from a completed project image run. Reference
        must be one of its actual dataset IDs.
        """
        async with async_session_factory() as db:
            user = await db.get(User, live.owner)
            result = await visual_samples.similar(
                db, user, live.project, uuid.UUID(run_id), uuid.UUID(reference_dataset_id)
            )
        for item in result["items"]:
            if item["available"]:
                item["source"] = attach_result(
                    live, "dataset", item["dataset_id"], item["name"], item["dataset_id"]
                )
        return json_text(result)

    return [compare_project_images, read_image_similarity]
