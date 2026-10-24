"""外部链接：只记录页面地址和元数据，标星与标签进入当前使用者的口味。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

import httpx

from peach.external_links import (
    add_link, add_source, fetch_public_html, list_links, media_address, q_external_links,
    remove_link, require_play_page, resolve_playback, search_links, star_link, update_link,
    w_external_link,
)
from peach.external_play import PlayTickets, q_external_link_playback, rewrite_link_playlist
from peach.migrations import upgrade
from peach.profiles import bind_active, create_profile, reset_active
from peach.taste_preferences import preference_profile

ROOT = Path(__file__).resolve().parents[1]


class _Conn:
    def __init__(self, db: Path, write: bool):
        self.db = db
        self.write = write
        self.connection = None

    def __enter__(self):
        self.connection = sqlite3.connect(self.db)
        self.connection.row_factory = sqlite3.Row
        return self.connection

    def __exit__(self, exc_type, *_args):
        try:
            if self.write and exc_type is None:
                self.connection.commit()
            elif self.write:
                self.connection.rollback()
        finally:
            self.connection.close()


class _Contract:
    def __init__(self, db: Path):
        self.db = db
        self.busts = 0

    def cache_bust(self):
        self.busts += 1

    def read_connection(self):
        return _Conn(self.db, False)

    def write_transaction(self):
        return _Conn(self.db, True)


class _Stream:
    def __init__(self, status, headers, url, body=b""):
        self.status_code = status
        self.headers = headers
        self.url = url
        self.body = body
        self.read = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_bytes(self, *_args, **_kwargs):
        self.read = True
        if self.body:
            yield self.body


class _Client:
    def __init__(self, stream: _Stream):
        self.stream_obj = stream
        self.calls = []

    def stream(self, method, url):
        self.calls.append((method, url))
        return self.stream_obj

    def close(self):
        pass


class ExternalLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "ledger.db"
        self.media = self.root / "media"
        self.media.mkdir()
        self.store = self.root / "history.sqlite"
        upgrade(self.db, ROOT / "migrations")
        self.contract = _Contract(self.db)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def files(self):
        return sorted(path.name for path in self.root.iterdir())

    def test_page_link_keeps_metadata_stars_and_tags_without_a_media_file(self):
        def fetch(url):
            self.assertEqual(url, "https://example.com/work")
            return {
                "url": url, "title": "页面标题", "description": "一段说明", "site": "Example",
                "thumbnail_url": "https://cdn.example/cover.jpg",
                "keywords": ["人妻", "随便写的"], "code": "", "studio": "示例厂牌",
                "creator": "", "performers": ["演示女优"], "release_date": "2024-05-01",
                "duration": 3600,
            }

        with self.connect() as connection:
            item = add_link(connection, {"url": "https://example.com/work"}, fetch=fetch)
            star_link(connection, {"id": item["id"], "liked": True})
        self.assertEqual(self.files(), ["ledger.db", "media"])
        self.assertEqual(list(self.media.iterdir()), [])
        with self.connect() as connection:
            row = connection.execute(
                "SELECT location,path,medium,size,name,studio FROM asset WHERE id=?",
                (item["asset_id"],),
            ).fetchone()
            self.assertEqual(
                tuple(row),
                ("external", "https://example.com/work", "link", None, "页面标题", "示例厂牌"),
            )
            tags = [tag for tag, in connection.execute(
                "SELECT tag FROM asset_tag WHERE asset_id=? ORDER BY tag", (item["asset_id"],))]
            self.assertEqual(tags, ["人妻"])
            liked = connection.execute(
                "SELECT liked FROM asset_preference WHERE profile_id='local-default' AND asset_id=?",
                (item["asset_id"],),
            ).fetchone()[0]
            self.assertEqual(liked, 1)
            _strengths, names, evidence = preference_profile(connection, self.store, cache=False)
        self.assertIn("人妻", names["tag"].values())
        self.assertIn("示例厂牌", names["studio"].values())
        studio_id = next(eid for eid, name in names["studio"].items() if name == "示例厂牌")
        self.assertIn("观看与反馈", evidence["studio"][studio_id])
        self.assertFalse(self.store.exists())

    def test_each_viewer_keeps_a_separate_list_and_the_same_url_is_one_asset(self):
        payload = {
            "origin": "catalog", "url": "https://javdb.com/v/Exact", "title": "示例作品",
            "code": "EXM-001", "studio": "示例厂牌", "performers": [], "keywords": [],
        }
        other = {
            "origin": "catalog", "url": "https://javdb.com/v/Other", "title": "另一部",
            "code": "EXM-002", "studio": "别人厂牌", "performers": [], "keywords": [],
        }

        def boom(url):
            raise AssertionError(url)

        with self.connect() as connection:
            create_profile(connection, "乙")
            viewer = connection.execute(
                "SELECT id FROM profile WHERE name='乙'",
            ).fetchone()[0]
            first = add_link(connection, payload, fetch=boom)
        token = bind_active(viewer)
        try:
            with self.connect() as connection:
                second = add_link(connection, other, fetch=boom)
                star_link(connection, {"id": second["id"], "liked": True})
                shared = add_link(connection, payload, fetch=boom)
                own = [item["url"] for item in list_links(connection)["items"]]
            self.assertEqual(own, ["https://javdb.com/v/Exact", "https://javdb.com/v/Other"])
            self.assertEqual(shared["asset_id"], first["asset_id"])
            self.assertNotEqual(second["asset_id"], first["asset_id"])
            with self.connect() as connection:
                _strengths, names, _evidence = preference_profile(connection, self.store, cache=False)
            self.assertIn("别人厂牌", names["studio"].values())
        finally:
            reset_active(token)
        with self.connect() as connection:
            visible = [item["title"] for item in list_links(connection)["items"]]
            assets = connection.execute(
                "SELECT count(*) FROM asset WHERE location='external' AND path=?",
                ("https://javdb.com/v/Exact",),
            ).fetchone()[0]
            _strengths, names, _evidence = preference_profile(connection, self.store, cache=False)
        self.assertEqual(visible, ["示例作品"])
        self.assertEqual(assets, 1)
        self.assertNotIn("别人厂牌", names["studio"].values())

    def test_media_bodies_and_private_redirects_are_refused(self):
        stream = _Stream(200, {"content-type": "video/mp4"}, "https://example.com/movie.mp4", b"not-a-file")
        client = _Client(stream)
        with self.assertRaisesRegex(ValueError, "媒体文件"):
            fetch_public_html("https://example.com/movie.mp4", client=client, resolve=lambda _host: True)
        self.assertFalse(stream.read)
        self.assertEqual(list(self.media.iterdir()), [])

        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.host)
            if request.url.host == "example.com":
                return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
            return httpx.Response(200, text="secret")

        with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as http:
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                fetch_public_html("https://example.com/jump", client=http, resolve=lambda host: host == "example.com")
        self.assertEqual(seen, ["example.com"])

    def test_catalog_search_saves_the_page_url_without_magnets_or_a_download(self):
        class Api:
            def search_movies(self, query, limit=10):
                self.query, self.limit = query, limit
                return [{
                    "id": "Exact", "number": "EXM-001", "title": "目录作品",
                    "maker_name": "示例厂牌", "actors": [{"name": "演示女优"}], "tags": ["人妻"],
                }]

            def movie(self, code):
                raise AssertionError(code)

            def magnets(self, code):
                raise AssertionError(code)

        api = Api()
        found = search_links("EXM-001", api=api)
        self.assertEqual(api.query, "EXM-001")
        self.assertEqual(api.limit, 5)
        self.assertEqual(found["hits"][0]["url"], "https://javdb.com/v/Exact")
        hit = found["hits"][0]
        with self.connect() as connection:
            item = w_external_link(self.contract, {"action": "add", "origin": "catalog", **hit})
        self.assertEqual(self.contract.busts, 2)
        self.assertEqual(item["code"], "EXM-001")
        self.assertEqual(item["tags"], ["人妻"])
        self.assertEqual(list(self.media.iterdir()), [])
        listed = q_external_links(self.contract, {"q": "人妻"})
        self.assertEqual([row["id"] for row in listed["items"]], [item["id"]])

    def test_edit_remove_and_rejected_addresses(self):
        def fetch(_url):
            raise AssertionError("http")

        with self.connect() as connection:
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                add_link(connection, {"url": "http://example.com/a"}, fetch=fetch)
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                add_link(connection, {"url": "https://127.0.0.1/a"}, fetch=fetch)
            item = add_link(connection, {"url": "https://example.com/a"}, fetch=lambda url: {
                "url": url, "title": "旧标题", "description": "", "site": "Example",
                "thumbnail_url": "", "keywords": [], "code": "", "studio": "",
                "creator": "", "performers": [], "release_date": "", "duration": None,
            })
            with self.assertRaisesRegex(ValueError, "已经收录"):
                add_link(connection, {"url": "https://example.com/a"}, fetch=lambda url: {
                    "url": url, "title": "重复", "description": "", "site": "",
                    "thumbnail_url": "", "keywords": [], "code": "", "studio": "",
                    "creator": "", "performers": [], "release_date": "", "duration": None,
                })
            edited = update_link(connection, {
                "id": item["id"], "title": "新标题", "description": "补充", "site": "Example",
            })
            self.assertEqual(edited["title"], "新标题")
            remove_link(connection, {"id": item["id"]})
            left = connection.execute(
                "SELECT count(*) FROM asset WHERE location='external'",
            ).fetchone()[0]
            self.assertEqual(left, 0)
            self.assertEqual(list_links(connection)["items"], [])

    def test_source_site_plays_the_address_written_on_the_page(self):
        page = "https://cdn.example/work"
        html = (
            '<meta property="og:video" content="https://cdn.example/play.m3u8?token=1">'
            '<video src="https://cdn.example/clip.mp4"></video>'
            "<script>seek 123e4567-e89b-12d3-a456-426614174000</script>"
            "https://127.0.0.1/secret.mp4"
        )
        self.assertEqual(media_address(page, html), "https://cdn.example/play.m3u8?token=1")
        self.assertNotIn("surrit", media_address(page, html))

        def fetch(url):
            return {
                "url": url, "title": "示例页面", "description": "", "site": "cdn.example",
                "thumbnail_url": "", "keywords": [], "code": "", "studio": "",
                "creator": "", "performers": [], "release_date": "", "duration": None,
            }

        with self.connect() as connection:
            item = add_link(connection, {"url": page}, fetch=fetch)
            with self.assertRaisesRegex(ValueError, "片源站点"):
                require_play_page(connection, item["id"])
            with self.assertRaisesRegex(ValueError, "公网域名"):
                add_source(connection, {"host": "127.0.0.1"}, resolve=lambda _host: True)
            added = add_source(
                connection, {"host": "https://www.cdn.example/path"},
                resolve=lambda host: host == "cdn.example",
            )
            self.assertEqual(added["sources"], ["cdn.example"])
            self.assertTrue(list_links(connection)["items"][0]["playable"])
            self.assertEqual(require_play_page(connection, item["id"]), page)
        found = resolve_playback(page, fetch=lambda url: {
            "url": url, "playback_url": media_address(page, html),
        })
        self.assertEqual(found, "https://cdn.example/play.m3u8?token=1")
        from peach import external_play
        original = external_play.resolve_playback
        external_play.resolve_playback = lambda _page: found
        try:
            opened = q_external_link_playback(self.contract, {"id": item["id"]})
        finally:
            external_play.resolve_playback = original
        self.assertEqual(opened, {
            "src": f"/link-play/{item['id']}", "type": "application/x-mpegURL",
        })
        state = type("State", (), {"link_play_tickets": PlayTickets()})()
        playlist = rewrite_link_playlist(
            state, b"#EXTM3U\n#EXTINF:1,\nhttps://cdn.example/a.ts\n",
            "https://cdn.example/play.m3u8", item["id"], page,
            resolve=lambda host: host == "cdn.example",
        )
        self.assertNotIn(b"cdn.example", playlist)
        self.assertIn(f"/link-play/{item['id']}/".encode(), playlist)
        with self.assertRaisesRegex(ValueError, "不可访问"):
            rewrite_link_playlist(
                state, b"#EXTM3U\n#EXTINF:1,\nhttps://127.0.0.1/a.ts\n",
                "https://cdn.example/play.m3u8", item["id"], page,
                resolve=lambda host: host == "cdn.example",
            )
        self.assertEqual(self.files(), ["ledger.db", "media"])
        self.assertEqual(list(self.media.iterdir()), [])
