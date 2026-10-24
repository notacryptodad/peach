"""口味看板：用已有偏好维度，向公开目录做有界检索。"""
from __future__ import annotations

import sqlite3
from contextlib import nullcontext

from .catalog_rules import normalise_code_key
from .javdb_public import PUBLIC_API, PublicBudgetExceeded, PublicRequestBudget, PublicSourceUnavailable
from .taste_preferences import LABELS, preference_profile, preference_score

SEARCH_KINDS = ("performer", "tag", "studio", "series")
QUERY_LIMIT = 3
DETAIL_LIMIT = 4
RESULT_LIMIT = 12
SOURCE = {
    "id": "javdb-public",
    "name": "JavDB 公开目录",
    "anonymous": True,
    "api_key": False,
    "interval_seconds": 3,
}
LENGTH_NAMES = {"0": "30 分钟内", "1": "30–90 分钟", "2": "90–150 分钟", "3": "150 分钟以上"}


def search_terms(strengths, names, *, limit: int = QUERY_LIMIT) -> list[dict[str, str]]:
    """按强度挑选可检索的口味词。同一类最多两个出演者，其余各类一个。"""
    rows = []
    for kind in SEARCH_KINDS:
        for identity, strength in strengths.get(kind, {}).items():
            label = str(names.get(kind, {}).get(identity, "")).strip()
            if label and strength > 0:
                rows.append((float(strength), kind, label[:40]))
    rows.sort(key=lambda row: (-row[0], row[1], row[2]))
    chosen: list[dict[str, str]] = []
    per_kind: dict[str, int] = {}
    for _strength, kind, label in rows:
        cap = 2 if kind == "performer" else 1
        if per_kind.get(kind, 0) >= cap:
            continue
        if any(label.casefold() == item["name"].casefold() for item in chosen):
            continue
        chosen.append({"kind": kind, "name": label})
        per_kind[kind] = per_kind.get(kind, 0) + 1
        if len(chosen) >= limit:
            break
    return chosen


def _dimension_name(kind: str, identity, names) -> str:
    label = names.get(kind, {}).get(identity)
    if label:
        return str(label)
    if kind == "era" and str(identity).isdigit():
        year = int(identity)
        return f"{year}–{year + 4}"
    if kind == "length":
        return LENGTH_NAMES.get(str(identity), str(identity))
    return str(identity)


def _profile(strengths, names, evidence) -> dict:
    dimensions = []
    for kind, label in LABELS.items():
        ranked = sorted(strengths.get(kind, {}).items(), key=lambda item: (-item[1], str(item[0])))[:5]
        items = [{
            "name": _dimension_name(kind, identity, names),
            "strength": round(float(strength), 4),
            "evidence": list(evidence.get(kind, {}).get(identity) or []),
        } for identity, strength in ranked if strength > 0]
        if items:
            dimensions.append({"kind": kind, "label": label, "items": items})
    return {"dimensions": dimensions}


def _onboarding() -> dict:
    return {
        "title": "口味还不足以推荐作品",
        "detail": "读取浏览记录或导入书签后，这里会按出演者、标签、厂牌和系列去在线目录找作品。本地媒体库可以稍后添加。",
        "steps": [
            {"title": "读取浏览记录", "detail": "使用这一页的读取或导入。"},
            {"title": "添加关注", "href": "/subscriptions?tab=add"},
            {"title": "添加本地媒体库", "href": "/settings/library", "secondary": True},
        ],
    }


def _band(score: int) -> str:
    if score >= 75:
        return "强烈推荐"
    if score >= 50:
        return "推荐"
    if score >= 25:
        return "值得看看"
    return "探索"


def _bind(kind, label, strengths, names, features, connection) -> None:
    text = str(label or "").strip()
    if not text:
        return
    folded = text.casefold()
    for identity, known in names.get(kind, {}).items():
        if str(known).casefold() == folded and identity in strengths.get(kind, {}):
            features.setdefault(kind, set()).add(identity)
            return
    try:
        from . import entities
        found = entities.resolve_entity(connection, kind, text)
    except sqlite3.Error:
        found = None
    if found is not None:
        features.setdefault(kind, set()).add(int(found["id"]))


def _actors(connection, source) -> list[dict]:
    from .javdb_public import actor_gender
    from .web_jav_detail import visible_actors
    try:
        return visible_actors(connection, source)
    except sqlite3.Error:
        return [actor for actor in source.get("actors") or []
                if isinstance(actor, dict) and actor.get("name") and actor_gender(actor) != "male"]


def _project(connection, source, query, strengths, names, evidence) -> dict | None:
    code = normalise_code_key(source.get("number") or source.get("code"))
    if not code:
        return None
    from .web_jav_detail import _source_fields
    actors = _actors(connection, source)
    try:
        from .genre_decisions import load_genre_decisions
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        decisions = load_genre_decisions(connection) if "genre_decision" in tables else {}
    except sqlite3.Error:
        decisions = {}
    fields = _source_fields(source, decisions)
    features: dict = {}
    for actor in actors:
        _bind("performer", actor.get("name"), strengths, names, features, connection)
        _bind("creator", actor.get("name"), strengths, names, features, connection)
    for tag in fields.get("tag_refs") or []:
        _bind("tag", tag.get("name"), strengths, names, features, connection)
    for tag in fields.get("tags") or []:
        _bind("tag", tag, strengths, names, features, connection)
    _bind("studio", source.get("maker_name") or source.get("studio"), strengths, names, features, connection)
    _bind("series", source.get("series_name"), strengths, names, features, connection)
    # 只有番号和标题的检索卡没有身份字段，用这次检索词本身作为匹配证据。
    if not (actors or fields.get("tags") or source.get("maker_name") or source.get("studio") or source.get("series_name")):
        _bind(query["kind"], query["name"], strengths, names, features, connection)
    if not any(features.get(kind, set()) & set(strengths.get(kind, {}))
               for kind in ("performer", "creator", "tag", "studio", "series")):
        return None
    minutes = source.get("duration")
    try:
        duration = float(minutes) * 60 if minutes else None
    except (TypeError, ValueError):
        duration = None
    score, reasons = preference_score(
        {"entities": features, "release_date": source.get("release_date"), "duration": duration},
        strengths, names, evidence)
    if score <= 0:
        return None
    from .web_jav_detail import discovery_cover_url
    shown = round(score * 100)
    performer_names = [str(actor.get("name")) for actor in actors if actor.get("name")]
    return {
        "code": code,
        "title": str(source.get("title") or source.get("origin_title") or code),
        "performers": "、".join(performer_names),
        "studio": str(source.get("maker_name") or source.get("studio") or ""),
        "release_date": str(source.get("release_date") or ""),
        "tags": [str(tag.get("name") or tag) for tag in (fields.get("tag_refs") or fields.get("tags") or [])][:6],
        "taste_score": shown,
        "band": _band(shown),
        "reasons": reasons[:3],
        "href": "/works/" + code,
        "cover_url": discovery_cover_url(
            {"code": code, "cover_url": source.get("cover_url"), "link": ""}, source),
        "source": SOURCE["id"],
    }


def _needs_detail(row: dict) -> bool:
    return not any(row.get(key) for key in ("actors", "tags", "maker_name", "studio", "series_name"))


def _collect(connection, api, terms, strengths, names, evidence):
    found: dict[str, dict] = {}
    queries = []
    details = 0
    try:
        budget = PublicRequestBudget(max_requests=16, max_bytes=2 * 1024 * 1024, max_seconds=45)
        with api.request_budget(budget):
            for term in terms:
                try:
                    rows = api.search_movies(term["name"], limit=10)
                except PublicSourceUnavailable as exc:
                    queries.append({**term, "status": "unavailable", "message": str(exc), "count": 0})
                    continue
                queries.append({**term, "status": "ok", "count": len(rows)})
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    source = row
                    if _needs_detail(row) and details < DETAIL_LIMIT:
                        code = normalise_code_key(row.get("number") or row.get("code"))
                        if code:
                            try:
                                source = api.movie(code)
                                details += 1
                            except PublicSourceUnavailable:
                                source = row
                    projected = _project(connection, source, term, strengths, names, evidence)
                    if projected is None:
                        continue
                    current = found.get(projected["code"])
                    if current is None or projected["taste_score"] > current["taste_score"]:
                        found[projected["code"]] = projected
    except PublicBudgetExceeded:
        if not queries:
            queries.append({"kind": "", "name": "", "status": "unavailable", "message": "本轮作品资料预算已用完", "count": 0})
    recommendations = sorted(found.values(), key=lambda row: (-row["taste_score"], row["code"]))[:RESULT_LIMIT]
    for rank, row in enumerate(recommendations, 1):
        row["rank"] = rank
    return recommendations, queries


def q_taste_board(contract, args=None, *, api=None) -> dict:
    """只读看板。没有可检索的口味词时不访问目录。"""
    del args
    from .profiles import viewer_files
    api = PUBLIC_API if api is None else api
    with contract.read_connection() as connection:
        _profile_id, store, _manifest = viewer_files(contract, connection)
        strengths, names, evidence = preference_profile(connection, store)
        profile = _profile(strengths, names, evidence)
        terms = search_terms(strengths, names)
        if not terms:
            return {
                "thin": True, "status": "thin", "profile": profile, "recommendations": [],
                "queries": [], "source": SOURCE, "onboarding": _onboarding(), "message": "",
            }
        root = getattr(contract, "cover_root", None)
        from .javdb_public import public_cache_scope
        scope = public_cache_scope(root.parent / "jav-public") if root is not None else nullcontext()
        with scope:
            recommendations, queries = _collect(connection, api, terms, strengths, names, evidence)
    any_ok = any(row["status"] == "ok" for row in queries)
    if recommendations:
        status, message = "ready", ""
    elif any_ok:
        status, message = "empty", "这次检索没有对上口味的作品。"
    else:
        status, message = "unavailable", "在线目录暂时没有返回作品。"
    return {
        "thin": False, "status": status, "profile": profile, "recommendations": recommendations,
        "queries": queries, "source": SOURCE, "onboarding": None, "message": message,
    }
