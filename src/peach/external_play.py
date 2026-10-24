"""片源页面的按需播放。上游地址留在服务端，媒体字节不落盘。"""
from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from collections import OrderedDict
from urllib.parse import urlsplit

import httpx
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, Response, StreamingResponse

from .external_links import media_type, require_play_page, resolve_playback
from .follow_stream import FollowProxyError, ResolvedFollowMedia, open_upstream
from .http import public_https_url, resolves_publicly
from .preview_cache import rewrite_hls
from .profiles import bind_active, reset_active
from .remote_media_cache import MEDIA_HEADERS

PLAYLIST_TYPES = frozenset({
    "application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl",
})
PLAYLIST_LIMIT = 2 * 1024 * 1024
TICKET_SECONDS = 2 * 3600


class PlayTickets:
    """HLS 子资源的短键。过期后请使用者重新打开播放。"""

    def __init__(self, capacity: int = 8192):
        self.entries = OrderedDict()
        self.lock = threading.Lock()
        self.capacity = capacity

    def add(self, link_id: int, page: str, url: str) -> str:
        key = hashlib.sha256(f"{link_id}\n{page}\n{url}".encode()).hexdigest()
        with self.lock:
            self.entries.pop(key, None)
            self.entries[key] = (time.monotonic() + TICKET_SECONDS, link_id, page, url)
            while len(self.entries) > self.capacity:
                self.entries.popitem(last=False)
        return key

    def get(self, key: str, link_id: int, page: str) -> str:
        with self.lock:
            row = self.entries.get(key)
        if row is None or row[0] <= time.monotonic() or row[1:3] != (link_id, page):
            raise ValueError("播放地址已失效，请重新打开")
        return row[3]


def tickets(state) -> PlayTickets:
    box = getattr(state, "link_play_tickets", None)
    if box is None:
        box = PlayTickets()
        state.link_play_tickets = box
    return box


def public_media(url: str, resolve=resolves_publicly) -> bool:
    if not public_https_url(url):
        return False
    return bool(resolve(urlsplit(url).hostname or ""))


def rewrite_link_playlist(state, body: bytes, base: str, link_id: int, page: str,
                          *, resolve=resolves_publicly) -> bytes:
    def link(target, _nested):
        if not public_media(target, resolve):
            raise ValueError("播放列表包含不可访问的媒体地址")
        key = tickets(state).add(link_id, page, target)
        return f"/link-play/{link_id}/{key}"

    return rewrite_hls(body, base, link)


def _page(request, link_id: int) -> str:
    from .profiles import PROFILE_COOKIE
    token = bind_active(request.cookies.get(PROFILE_COOKIE, ""))
    try:
        with request.app.state.database.read_connection() as connection:
            return require_play_page(connection, link_id)
    finally:
        reset_active(token)


def proxy_link(request, link_id: int, resource: str = ""):
    """把当前使用者的一条外部链接播出去。子资源只认短键。"""
    upstream = None
    try:
        page = _page(request, link_id)
        if resource:
            target_url = tickets(request.app.state).get(resource, link_id, page)
        else:
            target_url = resolve_playback(page)
        upstream = open_upstream(
            request.app.state.http_transport.client, request.method,
            ResolvedFollowMedia(target_url, page, public_hosts=True),
            incoming=request.headers,
            allowed=lambda url: public_media(url),
            accepted_statuses=(416,), strip_credentials=True)
        headers = {name: upstream.headers[name] for name in MEDIA_HEADERS if name in upstream.headers}
        headers.update({"cache-control": "no-store", "referrer-policy": "no-referrer"})
        if upstream.status_code == 416:
            upstream.close()
            return Response(status_code=416, headers={
                name: value for name, value in headers.items()
                if name not in {"content-type", "content-length", "content-encoding"}
            })
        content_type = upstream.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        playlist = (not resource and (
            content_type in PLAYLIST_TYPES or urlsplit(str(upstream.url)).path.lower().endswith(".m3u8")
            or urlsplit(target_url).path.lower().endswith(".m3u8")))
        if request.method == "HEAD":
            upstream.close()
            return Response(status_code=upstream.status_code, headers=headers)
        if playlist:
            try:
                content = bytearray()
                for chunk in upstream.iter_bytes():
                    content.extend(chunk)
                    if len(content) > PLAYLIST_LIMIT:
                        raise ValueError("播放列表过大")
            finally:
                upstream.close()
            body = rewrite_link_playlist(
                request.app.state, bytes(content), str(upstream.url), link_id, page)
            return Response(body, media_type="application/vnd.apple.mpegurl", headers={
                "cache-control": "no-store", "referrer-policy": "no-referrer",
                "content-length": str(len(body)),
            })

        async def generate():
            try:
                iterator = iter((upstream.content,)) if upstream.is_stream_consumed else upstream.iter_raw()
                while True:
                    chunk = await asyncio.to_thread(next, iterator, None)
                    if chunk is None:
                        break
                    yield chunk
            finally:
                upstream.close()

        return StreamingResponse(
            generate(), status_code=upstream.status_code, headers=headers,
            background=BackgroundTask(upstream.close))
    except ValueError as exc:
        if upstream is not None:
            upstream.close()
        return JSONResponse({"error": str(exc)}, status_code=404)
    except (FollowProxyError, OSError, httpx.HTTPError):
        if upstream is not None:
            upstream.close()
        return JSONResponse({"error": "播放地址没有返回"}, status_code=502)


def q_external_link_playback(contract, args=None) -> dict:
    link_id = int((args or {}).get("id") or 0)
    with contract.read_connection() as connection:
        page = require_play_page(connection, link_id)
    media = resolve_playback(page)
    return {"src": f"/link-play/{link_id}", "type": media_type(media)}
