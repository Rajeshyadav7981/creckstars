"""Shared httpx.AsyncClient with connection pooling.

A fresh `AsyncClient()` per call costs a TLS handshake every time
(~150–300 ms). Reusing a single pooled client across the process keeps
keep-alive connections warm and removes that per-call cost for VerifyNow,
Nominatim, Expo push, etc.
"""
import httpx

_client: httpx.AsyncClient | None = None
_LIMITS = httpx.Limits(max_connections=50, max_keepalive_connections=20)


def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=15.0, limits=_LIMITS)
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        c = _client
        _client = None
        await c.aclose()
