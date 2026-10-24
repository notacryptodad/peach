"""本机多名使用者：浏览口味分开，未知身份回到默认。"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from peach.profiles import (
    PROFILE_LIMIT, bind_active, create_profile, reset_active, resolve_profile, store_for,
    w_profile_activate,
)
from peach.routes_api import _viewer_response
from peach.taste_online import q_taste_board


class _Budget:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Catalog:
    def __init__(self):
        self.queries = []

    def request_budget(self, _budget):
        return _Budget()

    def search_movies(self, query, limit=10):
        self.queries.append(query)
        return []

    def movie(self, code):
        raise AssertionError(code)


class ViewerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = self.root / "history.sqlite"
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript(
            "CREATE TABLE app_user(id TEXT PRIMARY KEY, display_name TEXT, created_at TEXT);"
            "CREATE TABLE profile(id TEXT PRIMARY KEY, user_id TEXT, name TEXT, is_default INTEGER,"
            " settings_json TEXT, created_at TEXT, updated_at TEXT, UNIQUE(user_id, name));"
            "INSERT INTO app_user VALUES('local','Local user','2026-01-01T00:00:00Z');"
            "INSERT INTO profile VALUES('local-default','local','Default',1,'{}','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z');"
            "CREATE TABLE entity(id INTEGER PRIMARY KEY,kind TEXT,canonical_name TEXT,normalized_name TEXT);"
            "CREATE TABLE entity_alias(entity_id INTEGER,alias TEXT);"
            "CREATE TABLE entity_link(entity_id INTEGER,url TEXT);"
            "CREATE TABLE follow_source(entity_id INTEGER,url TEXT,enabled INTEGER);"
            "CREATE TABLE asset(id INTEGER PRIMARY KEY,creator TEXT,studio TEXT,duration REAL,release_date TEXT,"
            "play_count INTEGER,play_seconds REAL,o_count INTEGER,rating INTEGER,feedback TEXT,last_played REAL,disposal TEXT,medium TEXT);"
            "CREATE TABLE asset_preference(profile_id TEXT,asset_id INTEGER,liked INTEGER);"
            "CREATE TABLE asset_entity(asset_id INTEGER,entity_id INTEGER);"
        )
        self.contract = SimpleNamespace(read_connection=lambda: self.db, taste_history_store=self.store)

    def test_a_new_viewer_gets_a_separate_history_file(self):
        created = create_profile(self.db, "甲")
        self.db.commit()
        self.assertTrue(created["id"].startswith("local-"))
        self.assertNotEqual(created["id"], "local-default")
        self.assertEqual(store_for(self.store, "local-default"), self.store)
        self.assertEqual(store_for(self.store, created["id"]).name, f"history-{created['id']}.sqlite")

    def test_duplicate_and_blank_names_are_refused(self):
        create_profile(self.db, "甲")
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "同名"):
            create_profile(self.db, "甲")
        with self.assertRaisesRegex(ValueError, "1–40"):
            create_profile(self.db, "  ")

    def test_the_eighth_extra_viewer_is_refused(self):
        for index in range(PROFILE_LIMIT - 1):
            create_profile(self.db, f"使用者{index}")
            self.db.commit()
        with self.assertRaisesRegex(ValueError, "8"):
            create_profile(self.db, "再一个")

    def test_an_unknown_id_stays_on_the_default_viewer(self):
        token = bind_active("../history")
        try:
            self.assertEqual(resolve_profile(self.db), "local-default")
        finally:
            reset_active(token)
        with self.assertRaisesRegex(ValueError, "没有这个使用者"):
            w_profile_activate(self.contract, {"id": "local-missing"})

    def test_switching_sets_a_cookie(self):
        created = create_profile(self.db, "甲")
        self.db.commit()
        response = _viewer_response(w_profile_activate(self.contract, {"id": created["id"]}))
        self.assertIn(f"peach_profile={created['id']}", response.headers["set-cookie"])

    def test_a_new_viewer_does_not_inherit_the_library_taste(self):
        self.db.execute(
            "INSERT INTO entity VALUES(1,'performer','演示女优','演示女优')")
        self.db.execute(
            "INSERT INTO asset VALUES(1,NULL,NULL,7200,'2024-01-01',3,1800,0,0,NULL,NULL,NULL,'video')")
        self.db.execute("INSERT INTO asset_entity VALUES(1,1)")
        self.db.commit()
        created = create_profile(self.db, "甲")
        self.db.commit()
        catalog = _Catalog()
        token = bind_active(created["id"])
        try:
            payload = q_taste_board(self.contract, api=catalog)
        finally:
            reset_active(token)
        self.assertEqual(payload["status"], "thin")
        self.assertEqual(catalog.queries, [])
        home = _Catalog()
        q_taste_board(self.contract, api=home)
        self.assertTrue(home.queries)
