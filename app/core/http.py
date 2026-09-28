"""Bounded JSON responses; never expose provider bodies or request URLs in errors."""

import json

import httpx


class UpstreamError(RuntimeError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"Upstream returned HTTP {status_code}")


async def request_json(
    method: str, url: str, *, max_bytes: int = 2_000_000, allowed_status=(), **kwargs
):
    headers = dict(kwargs.pop("headers", {}))
    headers["Accept-Encoding"] = "identity"
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20, connect=5), follow_redirects=False, trust_env=False
    ) as client:
        async with client.stream(method, url, headers=headers, **kwargs) as response:
            if response.status_code in allowed_status:
                return {"_status": response.status_code}
            if not 200 <= response.status_code < 300:
                raise UpstreamError(response.status_code)
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise ValueError("Compressed upstream responses are not accepted")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise ValueError("Upstream response exceeds size limit")
            if not data:
                return {}
            parsed = json.loads(data)
            if not isinstance(parsed, dict):
                raise ValueError("Upstream response must be an object")
            return parsed
