"""Explicit, non-generating provider diagnostics. No configuration is saved."""

import asyncio
import json
import time

import httpx

from app.research.egress import ENDPOINTS

MODEL_ENDPOINTS = {
    "deepseek": "https://api.deepseek.com/models",
    "mimo": "https://api.xiaomimimo.com/v1/models",
    "opencode_zen": "https://opencode.ai/zen/v1/models",
}
BALANCE_ENDPOINT = "https://api.deepseek.com/user/balance"
MAX_BYTES = 256000


class ProbeError(Exception):
    def __init__(self, code, reachable=False):
        self.code, self.reachable = code, reachable


async def request(client, url, headers, method="GET"):
    async with client.stream(method, url, headers=headers) as response:
        status = response.status_code
        if status in (401, 403):
            raise ProbeError("authentication_failed", True)
        if status == 402:
            raise ProbeError("balance_or_plan", True)
        if status == 429:
            raise ProbeError("rate_limited", True)
        if 300 <= status < 400:
            raise ProbeError("redirect_refused", True)
        if status >= 500:
            raise ProbeError("provider_unavailable", True)
        if method == "HEAD":
            return status, None
        if status in (404, 405):
            raise ProbeError("inspection_unsupported", True)
        if status != 200:
            raise ProbeError("provider_rejected", True)
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > MAX_BYTES:
                raise ProbeError("response_too_large", True)
        try:
            value = json.loads(content)
        except (ValueError, UnicodeError):
            raise ProbeError("unexpected_response", True) from None
        if not isinstance(value, dict):
            raise ProbeError("unexpected_response", True)
        return status, value


async def probe(provider: str, model: str, secret: str, timeout_ms: int):
    started = time.monotonic()
    authenticated = None
    model_found = None
    reachable = False
    balance_available = None
    code = "connection_failed"
    headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
    headers["api-key" if provider == "mimo" else "Authorization"] = (
        secret if provider == "mimo" else "Bearer " + secret
    )
    # Fixed metadata endpoints only; no prompt, research data or paid generation.
    try:
        async with asyncio.timeout(min(timeout_ms, 20000) / 1000):
            async with httpx.AsyncClient(
                trust_env=False, follow_redirects=False, timeout=min(timeout_ms, 20000) / 1000
            ) as client:
                if provider == "deepseek":
                    _, balance = await request(client, BALANCE_ENDPOINT, headers)
                    reachable = True
                    if type(balance.get("is_available")) is not bool:
                        raise ProbeError("unexpected_response", True)
                    authenticated = True
                    balance_available = balance["is_available"]
                if provider in MODEL_ENDPOINTS:
                    _, catalog = await request(client, MODEL_ENDPOINTS[provider], headers)
                    reachable = True
                    data = catalog.get("data")
                    if not isinstance(data, list) or any(
                        not isinstance(item, dict) or not isinstance(item.get("id"), str)
                        for item in data
                    ):
                        raise ProbeError("unexpected_response", True)
                    model_found = any(item["id"] == model for item in data)
                    code = (
                        "model_not_listed"
                        if not model_found
                        else "balance_empty"
                        if balance_available is False
                        else "verified"
                        if authenticated
                        else "catalog_only"
                    )
                else:
                    # GLM has no documented model-list/auth-check endpoint in the
                    # current profile. A HEAD result proves reachability only.
                    status, _ = await request(client, ENDPOINTS[provider], headers, "HEAD")
                    reachable = True
                    code = "endpoint_only" if status < 500 else "provider_unavailable"
    except ProbeError as error:
        code, reachable = error.code, reachable or error.reachable
        if code == "authentication_failed":
            authenticated = False
    except (httpx.TimeoutException, TimeoutError):
        code = "timeout"
    except httpx.ConnectError:
        code = "network_error"
    except httpx.HTTPError:
        code = "connection_failed"
    return {
        "code": code,
        "reachable": reachable,
        "authentication_verified": authenticated,
        "model_found": model_found,
        "balance_available": balance_available,
        "latency_ms": round((time.monotonic() - started) * 1000),
        "generation_checked": False,
        "configuration_saved": False,
    }
