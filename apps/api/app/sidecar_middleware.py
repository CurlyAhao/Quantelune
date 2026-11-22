"""Sidecar auth middleware — validates the X-Session-Token header.

In desktop mode, the Tauri shell generates a fresh token on each launch and
passes it to the sidecar via the SIDECAR_SESSION_TOKEN env var. Every API
request must include this token in the X-Session-Token header, or it gets
a 401 response.

This is registered as a FastAPI middleware at startup.
"""

from __future__ import annotations

import os

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# Public paths that don't require the sidecar token (status, docs).
_PUBLIC_PATHS = {"/health", "/docs", "/redoc", "/openapi.json"}


class SidecarAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # No token configured → skip (dev mode or web production).
        session_token = os.environ.get("SIDECAR_SESSION_TOKEN", "")
        if not session_token:
            return await call_next(request)
        # Public paths are exempt.
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)
        # OPTIONS (CORS preflight) are exempt.
        if request.method == "OPTIONS":
            return await call_next(request)
        # HTML audio cannot attach the desktop session header. This grants only
        # one already-authorized, version-bound media resource, never other APIs.
        if request.method in {"GET", "HEAD"} and "/playback/" in request.url.path:
            from app.services.audio_playback import granted_path

            if granted_path(request.url.path):
                return await call_next(request)
        token = request.headers.get("X-Session-Token", "")
        if token != session_token:
            return Response(
                content=(
                    '{"detail":"Invalid sidecar session token.",'
                    '"error_code":"SIDECAR_UNAUTHORIZED"}'
                ),
                status_code=401,
                media_type="application/json",
            )
        return await call_next(request)
