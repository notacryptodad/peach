"""外部链接：记录页面地址和元数据，不把媒体文件写入本机。"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from .catalog_rules import normalise_code_key
from .http import body_text, public_https_url, resolves_publicly
from .profiles import resolve_profile
from .user_agent import USER_AGENT

LINK_LIMIT = 200
SEARCH_LIMIT = 5
PAGE_BYTES = 256 * 1024
TIMEOUT = 12.0
LOCATION = "external"
MEDIUM = "link"
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_MEDIA_TYPES = frozenset({
    "application/octet-stream",
    "application/x-bittorrent",
    "application/vnd.apple.mpegurl",
    "application/dash+xml",
    "application/x-mpegurl",
})
_CATALOG_URL = re.compile(r"https://javdb.com/v/[A-Za-z0-9]+")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TAG_SPLIT = re.compile(r"[,，、]")


def canonical_url(url: str) -> str:
    """公网 HTTPS 页面地址。去掉片段和账号，拒绝 IP、内网名和非 443 端口。"""
    text = " ".join(str(url or "").split())
    if not text or len(text) > 2000 or any(ord(char) < 33 or ord(char) == 127 for char in text):
        raise ValueError("只接受公网 HTTPS 地址")
    if not public_https_url(text):
        raise ValueError("只接受公网 HTTPS 地址")
    parts = urlsplit(text)
    if parts.port not in (None, 443):
        raise ValueError("只接受公网 HTTPS 地址")
    host = (parts.hostname or "").casefold().rstrip(".")
    path = parts.path or "/"
    query = f"?{parts.query}" if parts.query else ""
    return f"https://{host}{path}{query}"


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _clean(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _rows(connection, sql: str, params=()) -> list[dict]:
    cursor = connection.execute(sql, tuple(params))
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _content_type(headers) -> str:
    for key, value in headers.items():
        if str(key).lower() == "content-type":
            return str(value).split(";", 1)[0].strip().lower()
    return ""


def _reject_media(headers) -> None:
    kind = _content_type(headers)
    if kind.startswith(("video/", "audio/")) or kind in _MEDIA_TYPES:
        raise ValueError("这个地址是媒体文件，请改用作品页面地址")
    if kind and "html" not in kind and not kind.startswith("text/") and kind != "application/xhtml+xml":
        raise ValueError("这个地址不是网页")


def _read_bounded(chunks, limit: int) -> bytes:
    kept: list[bytes] = []
    total = 0
    for chunk in chunks:
        if total >= limit:
            break
        piece = chunk[: limit - total]
        kept.append(piece)
        total += len(piece)
        if total >= limit:
            break
    return b"".join(kept)


def _assert_public(url: str, resolve) -> None:
    canonical_url(url)
    host = urlsplit(url).hostname or ""
    if not resolve(host):
        raise ValueError("这个地址不在公网")


def page_metadata(url: str, body: bytes, headers) -> dict:
    """从已取到的页头里读标题和说明。不保存正文。"""
    from .social_links import meta_content
    text = body_text(body[:PAGE_BYTES], headers)
    page = canonical_url(url)
    host = urlsplit(page).hostname or ""
    title_tag = _TITLE.search(text)
    raw_title = ""
    if title_tag:
        raw_title = re.sub(r"<[^>]+>", " ", title_tag.group(1))
    title = _clean(meta_content(text, "og:title") or raw_title or host, 200)
    description = _clean(meta_content(text, "og:description") or meta_content(text, "description"), 500)
    site = _clean(meta_content(text, "og:site_name") or host, 80)
    image = meta_content(text, "og:image").strip()
    thumbnail = ""
    if image:
        try:
            thumbnail = canonical_url(str(httpx.URL(page).join(image)))
        except ValueError:
            thumbnail = ""
    keywords = [_clean(part, 80) for part in _TAG_SPLIT.split(meta_content(text, "keywords"))]
    published = _DATE.search(meta_content(text, "article:published_time") or "")
    duration = _duration(meta_content(text, "video:duration"))
    return {
        "url": page, "title": title or host, "description": description, "site": site,
        "thumbnail_url": thumbnail, "keywords": [item for item in keywords if item][:12],
        "code": "", "studio": "", "creator": "", "performers": [],
        "release_date": published.group(0) if published else "", "duration": duration,
    }


def _duration(value: str):
    text = str(value or "").strip()
    if not text or not re.fullmatch(r"\d+(\.\d+)?", text):
        return None
    seconds = float(text)
    if seconds <= 0 or seconds > 24 * 3600:
        return None
    return seconds


def fetch_public_html(url: str, *, client: httpx.Client | None = None, resolve=None) -> dict:
    """读取页面元数据。每一跳都重新核对公网 HTTPS，媒体响应不读正文。"""
    resolve = resolves_publicly if resolve is None else resolve
    current = canonical_url(url)
    owns = client is None
    client = client or httpx.Client(
        follow_redirects=False, timeout=TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    )
    try:
        for _hop in range(4):
            _assert_public(current, resolve)
            try:
                with client.stream("GET", current) as response:
                    if response.status_code in _REDIRECTS:
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("这个页面没有返回")
                        current = canonical_url(str(httpx.URL(current).join(location)))
                        continue
                    if response.status_code != 200:
                        raise ValueError("这个页面没有返回")
                    _reject_media(response.headers)
                    final = canonical_url(str(response.url))
                    _assert_public(final, resolve)
                    body = _read_bounded(response.iter_bytes(), PAGE_BYTES)
                    return page_metadata(final, body, dict(response.headers))
            except httpx.HTTPError as exc:
                raise ValueError("这个页面没有返回") from exc
        raise ValueError("这个页面跳转次数过多")
    finally:
        if owns:
            client.close()


def _known_tag(label: str) -> str:
    from .catalog_rules import current_tags, tag_cat
    canonical = current_tags([label])
    if not canonical:
        return ""
    chosen = canonical[0]
    if tag_cat(chosen) in {"general", "meta", "artist"}:
        return ""
    return chosen


def _attach(connection, asset_id: int, meta: dict, stamp: str) -> None:
    from .entities import upsert_asset_entity
    for name in (meta.get("performers") or [])[:8]:
        upsert_asset_entity(
            connection, kind="performer", name=name, asset_id=asset_id, role="performer",
            source="external-link", confidence=1.0, now=stamp,
        )
    if meta.get("studio"):
        upsert_asset_entity(
            connection, kind="studio", name=meta["studio"], asset_id=asset_id, role="studio",
            source="external-link", confidence=1.0, now=stamp,
        )
    for keyword in meta.get("keywords") or []:
        label = _known_tag(str(keyword))
        if not label:
            continue
        connection.execute(
            "INSERT OR IGNORE INTO asset_tag(asset_id,tag,confidence,source) VALUES(?,?,1.0,'external-link')",
            (asset_id, label),
        )
        upsert_asset_entity(
            connection, kind="tag", name=label, asset_id=asset_id, role="tag",
            source="external-link", confidence=1.0, now=stamp,
        )


def _owned(connection, link_id: int, profile_id: str) -> int:
    rows = _rows(
        connection,
        "SELECT asset_id FROM external_link WHERE id=? AND profile_id=?",
        (link_id, profile_id),
    )
    if not rows:
        raise ValueError("没有这条外部链接")
    return int(rows[0]["asset_id"])


def _tags_for(connection, profile_id: str, asset_ids: list[int]) -> dict[int, list[str]]:
    if not asset_ids:
        return {}
    from .entities import normalize_entity_name
    marks = ",".join("?" for _ in asset_ids)
    tags = _rows(
        connection,
        f"SELECT asset_id, tag FROM asset_tag WHERE asset_id IN ({marks}) ORDER BY tag",
        asset_ids,
    )
    hidden = _rows(
        connection,
        "SELECT asset_id, normalized_tag FROM asset_tag_preference "
        f"WHERE profile_id=? AND hidden=1 AND asset_id IN ({marks})",
        (profile_id, *asset_ids),
    )
    hidden_keys = {(int(row["asset_id"]), row["normalized_tag"]) for row in hidden}
    found: dict[int, list[str]] = {}
    for row in tags:
        asset_id = int(row["asset_id"])
        if (asset_id, normalize_entity_name(row["tag"])) in hidden_keys:
            continue
        found.setdefault(asset_id, []).append(row["tag"])
    return found


def _item(connection, profile_id: str, link_id: int) -> dict:
    rows = [row for row in list_links(connection, "")["items"] if row["id"] == link_id]
    if not rows:
        raise ValueError("没有这条外部链接")
    return rows[0]


def list_links(connection, query: str | None = None) -> dict:
    profile_id = resolve_profile(connection)
    rows = _rows(
        connection,
        "SELECT l.id, l.asset_id, l.url, l.title, l.description, l.site, "
        "COALESCE(l.thumbnail_url,'') thumbnail_url, COALESCE(a.code,'') code, "
        "COALESCE(a.studio,'') studio, COALESCE(a.creator,'') creator, "
        "COALESCE(a.release_date,'') release_date, COALESCE(p.liked,0) liked "
        "FROM external_link l JOIN asset a ON a.id=l.asset_id "
        "LEFT JOIN asset_preference p ON p.asset_id=a.id AND p.profile_id=l.profile_id "
        "WHERE l.profile_id=? ORDER BY l.updated_at DESC, l.id DESC",
        (profile_id,),
    )
    tags = _tags_for(connection, profile_id, [int(row["asset_id"]) for row in rows])
    needle = " ".join(str(query or "").split()).casefold()
    items = []
    for row in rows:
        item = {
            "id": int(row["id"]), "asset_id": int(row["asset_id"]), "url": row["url"],
            "title": row["title"], "description": row["description"], "site": row["site"],
            "thumbnail_url": row["thumbnail_url"], "code": row["code"], "studio": row["studio"],
            "creator": row["creator"], "release_date": row["release_date"],
            "liked": bool(row["liked"]), "tags": tags.get(int(row["asset_id"]), []),
            "href": row["url"],
        }
        if needle:
            haystack = "\n".join([
                item["title"], item["url"], item["site"], item["description"], item["code"],
                item["studio"], item["creator"], *item["tags"],
            ]).casefold()
            if needle not in haystack:
                continue
        items.append(item)
    return {"items": items[:LINK_LIMIT], "limit": LINK_LIMIT}


def _save(connection, profile_id: str, meta: dict) -> dict:
    url = canonical_url(meta["url"])
    if connection.execute(
        "SELECT 1 FROM external_link WHERE profile_id=? AND url=?", (profile_id, url),
    ).fetchone():
        raise ValueError("已经收录这个地址")
    count = connection.execute(
        "SELECT count(*) FROM external_link WHERE profile_id=?", (profile_id,),
    ).fetchone()[0]
    if int(count) >= LINK_LIMIT:
        raise ValueError("外部链接最多 200 条")
    stamp = _stamp()
    title = _clean(meta.get("title"), 200) or (urlsplit(url).hostname or url)
    description = _clean(meta.get("description"), 500)
    site = _clean(meta.get("site"), 80)
    thumbnail = meta.get("thumbnail_url") or ""
    if thumbnail:
        try:
            thumbnail = canonical_url(thumbnail)
        except ValueError:
            thumbnail = ""
    code = normalise_code_key(meta.get("code") or "")
    studio = _clean(meta.get("studio"), 80)
    performers = [_clean(name, 40) for name in (meta.get("performers") or [])]
    performers = [name for name in performers if name][:8]
    creator = _clean(meta.get("creator"), 80) or (performers[0] if performers else "")
    release = meta.get("release_date") or ""
    if not _DATE.fullmatch(str(release)[:10] if release else ""):
        release = ""
    else:
        release = str(release)[:10]
    duration = meta.get("duration")
    try:
        duration = float(duration) if duration not in (None, "") else None
    except (TypeError, ValueError):
        duration = None
    existing = _rows(
        connection, "SELECT id FROM asset WHERE location=? AND path=?", (LOCATION, url),
    )
    if existing:
        asset_id = int(existing[0]["id"])
    else:
        cursor = connection.execute(
            "INSERT INTO asset(location,path,name,medium,creator,studio,code,duration,"
            "release_date,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (LOCATION, url, title, MEDIUM, creator or None, studio or None, code or None,
             duration, release or None, stamp, stamp),
        )
        asset_id = int(cursor.lastrowid)
        _attach(connection, asset_id, {
            **meta, "studio": studio, "performers": performers, "keywords": meta.get("keywords") or [],
        }, stamp)
    cursor = connection.execute(
        "INSERT INTO external_link(profile_id,asset_id,url,title,description,site,thumbnail_url,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (profile_id, asset_id, url, title, description, site, thumbnail or None, stamp, stamp),
    )
    return _item(connection, profile_id, int(cursor.lastrowid))


def _catalog_meta(body: dict) -> dict:
    url = canonical_url(body.get("url"))
    if not _CATALOG_URL.fullmatch(url):
        raise ValueError("目录结果地址无效")
    performers = body.get("performers") or []
    if isinstance(performers, str):
        performers = _TAG_SPLIT.split(performers)
    keywords = body.get("keywords") or body.get("tags") or []
    if isinstance(keywords, str):
        keywords = _TAG_SPLIT.split(keywords)
    return {
        "url": url, "title": body.get("title") or "", "description": body.get("description") or "",
        "site": body.get("site") or "JavDB", "thumbnail_url": "",
        "keywords": keywords, "code": body.get("code") or "", "studio": body.get("studio") or "",
        "creator": "", "performers": performers, "release_date": body.get("release_date") or "",
        "duration": body.get("duration"),
    }


def add_link(connection, body: dict, *, fetch=fetch_public_html) -> dict:
    profile_id = resolve_profile(connection)
    if str((body or {}).get("origin") or "") == "catalog":
        meta = _catalog_meta(body)
    else:
        meta = fetch(canonical_url(str((body or {}).get("url") or "")))
    return _save(connection, profile_id, meta)


def update_link(connection, body: dict) -> dict:
    profile_id = resolve_profile(connection)
    link_id = int(body.get("id"))
    asset_id = _owned(connection, link_id, profile_id)
    title = _clean(body.get("title"), 200)
    if not title:
        raise ValueError("标题需为 1–200 个字符")
    description = _clean(body.get("description"), 500)
    site = _clean(body.get("site"), 80)
    stamp = _stamp()
    connection.execute(
        "UPDATE external_link SET title=?, description=?, site=?, updated_at=? WHERE id=?",
        (title, description, site, stamp, link_id),
    )
    connection.execute(
        "UPDATE asset SET name=? WHERE id=? AND location=?",
        (title, asset_id, LOCATION),
    )
    return _item(connection, profile_id, link_id)


def remove_link(connection, body: dict) -> dict:
    profile_id = resolve_profile(connection)
    link_id = int(body.get("id"))
    asset_id = _owned(connection, link_id, profile_id)
    connection.execute("DELETE FROM external_link WHERE id=?", (link_id,))
    connection.execute(
        "DELETE FROM asset_preference WHERE profile_id=? AND asset_id=?",
        (profile_id, asset_id),
    )
    if connection.execute(
        "SELECT 1 FROM external_link WHERE asset_id=?", (asset_id,),
    ).fetchone() is None:
        for table in ("asset_tag", "asset_entity", "asset_tag_preference", "asset_preference"):
            connection.execute(f"DELETE FROM {table} WHERE asset_id=?", (asset_id,))
        connection.execute(
            "DELETE FROM asset WHERE id=? AND location=?", (asset_id, LOCATION),
        )
    return {"ok": True, "id": link_id}


def star_link(connection, body: dict) -> dict:
    profile_id = resolve_profile(connection)
    link_id = int(body.get("id"))
    asset_id = _owned(connection, link_id, profile_id)
    liked = 1 if bool(body.get("liked")) else 0
    if not liked:
        connection.execute(
            "DELETE FROM asset_preference WHERE profile_id=? AND asset_id=?",
            (profile_id, asset_id),
        )
    else:
        connection.execute(
            "INSERT INTO asset_preference(profile_id,asset_id,liked,reason,source,updated_at) "
            "VALUES(?,?,?,'','web',strftime('%Y-%m-%dT%H:%M:%fZ','now')) "
            "ON CONFLICT(profile_id,asset_id) DO UPDATE SET "
            "liked=excluded.liked, source=excluded.source, updated_at=excluded.updated_at",
            (profile_id, asset_id, liked),
        )
    return {"ok": True, "id": link_id, "liked": bool(liked)}


def set_tag(connection, body: dict) -> dict:
    profile_id = resolve_profile(connection)
    link_id = int(body.get("id"))
    asset_id = _owned(connection, link_id, profile_id)
    operation = str(body.get("operation") or "")
    if operation not in {"add", "remove"}:
        raise ValueError("未知操作")
    label = _clean(body.get("tag"), 80)
    if not label or label.startswith("演员:"):
        raise ValueError("标签需为 1–80 个字符")
    from .catalog_rules import current_tags
    from .entities import normalize_entity_name, upsert_asset_entity
    if operation == "add":
        canonical = current_tags([label])
        if not canonical:
            raise ValueError("该标签不在当前词表中")
        label = canonical[0]
    normalized = normalize_entity_name(label)
    stamp = _stamp()
    if operation == "remove":
        connection.execute(
            "INSERT INTO asset_tag_preference(profile_id,asset_id,normalized_tag,hidden,updated_at) "
            "VALUES(?,?,?,1,?) ON CONFLICT(profile_id,asset_id,normalized_tag) "
            "DO UPDATE SET hidden=1, updated_at=excluded.updated_at",
            (profile_id, asset_id, normalized, stamp),
        )
    else:
        connection.execute(
            "DELETE FROM asset_tag_preference WHERE profile_id=? AND asset_id=? AND normalized_tag=?",
            (profile_id, asset_id, normalized),
        )
        connection.execute(
            "INSERT OR IGNORE INTO asset_tag(asset_id,tag,confidence,source) VALUES(?,?,1.0,'web-user')",
            (asset_id, label),
        )
        upsert_asset_entity(
            connection, kind="tag", name=label, asset_id=asset_id, role="tag",
            source="web-user", confidence=1.0, metadata={"profile_id": profile_id}, now=stamp,
        )
    tags = _tags_for(connection, profile_id, [asset_id]).get(asset_id, [])
    return {"ok": True, "id": link_id, "tag": label, "operation": operation, "tags": tags}


def catalog_hit(row: dict) -> dict | None:
    identity = row.get("id")
    if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9]+", identity):
        return None
    code = normalise_code_key(row.get("number") or row.get("code") or "")
    performers = []
    for actor in row.get("actors") or []:
        name = actor.get("name") if isinstance(actor, dict) else actor
        name = _clean(name, 40)
        if name:
            performers.append(name)
    keywords = []
    for tag in row.get("tags") or []:
        label = tag.get("name") if isinstance(tag, dict) else tag
        label = _clean(label, 80)
        if label:
            keywords.append(label)
    minutes = row.get("duration")
    try:
        duration = float(minutes) * 60 if minutes not in (None, "") else None
    except (TypeError, ValueError):
        duration = None
    release = str(row.get("release_date") or "")
    return {
        "url": f"https://javdb.com/v/{identity}",
        "title": _clean(row.get("title") or row.get("origin_title") or code or identity, 200),
        "description": "", "site": "JavDB", "thumbnail_url": "",
        "code": code, "studio": _clean(row.get("maker_name") or row.get("studio") or "", 80),
        "performers": performers[:8], "keywords": keywords[:12],
        "release_date": release[:10] if _DATE.fullmatch(release[:10]) else "",
        "duration": duration,
    }


def search_links(query: str | None, *, api=None, fetch=fetch_public_html) -> dict:
    text = " ".join(str(query or "").split())
    if not text:
        return {"mode": "empty", "hits": [], "preview": None}
    if text.lower().startswith(("http://", "https://")):
        return {"mode": "page", "hits": [], "preview": fetch(text)}
    if len(text) > 40:
        raise ValueError("检索词无效")
    if api is None:
        from .javdb_public import PUBLIC_API
        api = PUBLIC_API
    from .javdb_public import PublicSourceUnavailable
    try:
        rows = api.search_movies(text, limit=SEARCH_LIMIT)
    except PublicSourceUnavailable as exc:
        raise ValueError("在线目录暂时没有返回作品") from exc
    hits = [hit for row in rows if isinstance(row, dict) and (hit := catalog_hit(row))]
    return {"mode": "catalog", "hits": hits, "preview": None}


def q_external_links(contract, args=None) -> dict:
    with contract.read_connection() as connection:
        return list_links(connection, (args or {}).get("q"))


def q_external_link_search(contract, args=None) -> dict:
    return search_links((args or {}).get("q"), api=getattr(contract, "public_catalog", None))


def w_external_link(contract, body) -> dict:
    body = body or {}
    action = str(body.get("action") or "")
    bust = getattr(contract, "cache_bust", None)
    if callable(bust):
        bust()
    with contract.write_transaction() as connection:
        if action == "add":
            result = add_link(connection, body)
        elif action == "update":
            result = update_link(connection, body)
        elif action == "remove":
            result = remove_link(connection, body)
        elif action == "star":
            result = star_link(connection, body)
        elif action == "tag":
            result = set_tag(connection, body)
        else:
            raise ValueError("未知操作")
    if callable(bust):
        bust()
    return result
