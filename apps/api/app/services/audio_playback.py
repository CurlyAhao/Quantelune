"""Owned audio playback and bounded waveform reads on one native sample clock."""

import asyncio
import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path

from fastapi.responses import FileResponse

from app.exceptions import StatForgeError
from app.services import material_service, storage_service

_verified = {}
_previews = {}
_grants = {}
_locks = {}


class AudioFileResponse(FileResponse):
    """Keep the actual Range transfer count with the existing local diagnostics."""

    async def __call__(self, scope, receive, send):
        transferred, status, content_range = 0, None, None
        requested = dict(scope.get("headers", [])).get(b"range", b"").decode()
        # Browsers send an open-ended Range even for preload=metadata. Serve a
        # bounded chunk; subsequent native seek/read requests retain file offsets.
        match = re.fullmatch(r"bytes=(\d+)-(\d*)", requested)
        if match and scope["method"] == "GET":
            first = int(match[1])
            last = min(
                int(match[2]) if match[2] else Path(self.path).stat().st_size - 1,
                first + 1024 * 1024 - 1,
            )
            scope = {
                **scope,
                "headers": [(key, value) for key, value in scope["headers"] if key != b"range"]
                + [(b"range", f"bytes={first}-{last}".encode())],
            }

        async def tracked(message):
            nonlocal transferred, status, content_range
            if message["type"] == "http.response.start":
                status = message["status"]
                content_range = dict(message.get("headers", [])).get(b"content-range", b"").decode()
            if message["type"] == "http.response.body":
                transferred += len(message.get("body", b""))
            await send(message)

        try:
            await super().__call__(scope, receive, tracked)
        finally:
            from app.services.multimodal_cases import runtime_config

            record = {
                "time": time.time(),
                "material_id": scope.get("path_params", {}).get("material_id", ""),
                "range": requested,
                "status": status,
                "content_range": content_range,
                "sent_bytes": transferred,
            }
            with (Path(runtime_config()["temp"]) / "audio-range-requests.jsonl").open(
                "a", encoding="utf-8"
            ) as file:
                file.write(json.dumps(record, default=str) + "\n")


def signature(path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino


def fail(message="声音文件已变化，请重新打开材料。", status=409):
    return StatForgeError(status, "AUDIO_PLAYBACK_UNAVAILABLE", message)


async def verified_source(value):
    path = storage_service.get_local_object_path(value.object_key)
    try:
        stamp = signature(path)
    except OSError:
        raise fail("原声音文件找不到，请恢复原文件后重新打开。") from None
    key = (str(value.id), value.sha256, value.version, str(path), stamp)
    if key not in _verified:
        await material_service.verify_version(value)
        if signature(path) != stamp:
            raise fail()
        _verified[key] = True
    return path, key


async def preview(value, start=None, end=None):
    if not value.mime_type.startswith("audio/"):
        raise fail("此材料不是独立音频。", 422)
    from app.services.multimodal_cases import runtime_config

    source, source_key = await verified_source(value)
    key = (*source_key, start, end)
    async with _locks.setdefault(key, asyncio.Lock()):
        if key in _previews:
            return _previews[key]
        config = runtime_config()
        output = Path(config["temp"]) / "audio-previews" / uuid.uuid4().hex
        output.mkdir(parents=True, exist_ok=False)
        request = output / "request.json"
        request.write_text(
            json.dumps(
                {
                    "operation": "preview",
                    "source": str(source),
                    "ffmpeg": config["options"]["ffmpeg_path"],
                    "cache_root": str(Path(config["temp"]) / "audio-native"),
                    "output": str(output),
                    "start": start,
                    "end": end,
                }
            ),
            encoding="utf-8",
        )
        process = await asyncio.create_subprocess_exec(
            config["python"],
            "-B",
            str(Path(__file__).with_name("audio_pcm.py")),
            str(request),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                **os.environ,
                "TEMP": config["temp"],
                "TMP": config["temp"],
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
            },
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        try:
            _, stderr = await process.communicate()
        except asyncio.CancelledError:
            if process.returncode is None:
                process.terminate()
            await process.wait()
            raise
        if process.returncode:
            (output / "error.log").write_bytes(stderr)
            raise fail("声音读取失败，请检查文件能否正常解码及所选时间范围。", 422)
        if signature(source) != source_key[-1]:
            raise fail()
        result = json.loads((output / "preview.json").read_text(encoding="utf-8"))
        _previews[key] = result
        return result


def public_preview(result):
    return {k: v for k, v in result.items() if k not in {"path", "read_intervals"}}


async def issue(value):
    result = await preview(value)
    source, key = await verified_source(value)
    now = time.monotonic()
    for token, grant in list(_grants.items()):
        if grant["expires"] < now:
            del _grants[token]
    token = secrets.token_urlsafe(32)
    _grants[token] = {
        "material_id": str(value.id),
        "user_id": value.user_id,
        "sha256": value.sha256,
        "version": value.version,
        "source": source,
        "source_stamp": key[-1],
        "path": Path(result["path"]),
        "stamp": signature(Path(result["path"])),
        "expires": now + 12 * 3600,
    }
    return {**public_preview(result), "url": f"/api/v1/materials/{value.id}/playback/{token}"}


def granted_path(path):
    parts = path.split("/")
    if len(parts) != 7 or parts[1:4] != ["api", "v1", "materials"] or parts[5] != "playback":
        return False
    grant = _grants.get(parts[6])
    return bool(grant and grant["material_id"] == parts[4] and grant["expires"] > time.monotonic())


async def resolve(db, material_id, token):
    grant = _grants.get(token)
    if (
        not grant
        or grant["material_id"] != str(material_id)
        or grant["expires"] <= time.monotonic()
    ):
        raise fail("播放链接已失效，请重新打开材料。", 401)
    value = await material_service.load(db, material_id, grant["user_id"])
    try:
        valid = (
            value.sha256 == grant["sha256"]
            and value.version == grant["version"]
            and signature(grant["source"]) == grant["source_stamp"]
            and signature(grant["path"]) == grant["stamp"]
        )
    except OSError:
        valid = False
    if not valid:
        _grants.pop(token, None)
        raise fail()
    return grant["path"]
