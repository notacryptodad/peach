"""口味看板：薄口味不访问目录，有信号时按公开目录命中排序。"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from peach.javdb_public import PublicSourceUnavailable
from peach.taste_online import q_taste_board, search_terms


class _Budget:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Catalog:
    def __init__(self, rows=None, error=None):
        self.rows = rows or {}
        self.error = error
        self.queries = []
        self.details = []

    def request_budget(self, _budget):
        return _Budget()

    def search_movies(self, query, limit=10):
        self.queries.append(query)
        if self.error:
            raise self.error
        return list(self.rows.get(query, []))[:limit]

    def movie(self, code):
        self.details.append(code)
        raise AssertionError(code)


class TasteOnlineTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = self.root / "history.sqlite"
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript(
            "CREATE TABLE entity(id INTEGER PRIMARY KEY,kind TEXT,canonical_name TEXT,normalized_name TEXT);"
            "CREATE TABLE entity_alias(entity_id INTEGER,alias TEXT);"
            "CREATE TABLE entity_link(entity_id INTEGER,url TEXT);"
            "CREATE TABLE follow_source(entity_id INTEGER,url TEXT,enabled INTEGER);"
            "CREATE TABLE asset(id INTEGER PRIMARY KEY,creator TEXT,studio TEXT,duration REAL,release_date TEXT,"
            "play_count INTEGER,play_seconds REAL,o_count INTEGER,rating INTEGER,feedback TEXT,last_played REAL,disposal TEXT,medium TEXT);"
            "CREATE TABLE asset_preference(profile_id TEXT,asset_id INTEGER,liked INTEGER);"
            "CREATE TABLE asset_entity(asset_id INTEGER,entity_id INTEGER);"
        )
        self.db.executemany("INSERT INTO entity VALUES(?,?,?,?)", [
            (1, "performer", "演示女优", "演示女优"), (2, "tag", "恋足", "恋足"),
            (3, "studio", "演示厂牌", "演示厂牌")])
        self.contract = SimpleNamespace(read_connection=lambda: self.db, taste_history_store=self.store)

    def _watch(self):
        self.db.execute(
            "INSERT INTO asset VALUES(1,NULL,NULL,7200,'2024-01-01',3,1800,0,0,NULL,NULL,NULL,'video')")
        self.db.executemany("INSERT INTO asset_entity VALUES(?,?)", [(1, 1), (1, 2), (1, 3)])

    def test_thin_profile_does_not_query_the_catalog(self):
        catalog = _Catalog(error=AssertionError("空口味不应访问目录"))
        payload = q_taste_board(self.contract, api=catalog)
        self.assertEqual(payload["status"], "thin")
        self.assertEqual(payload["recommendations"], [])
        self.assertTrue(payload["onboarding"]["steps"])
        self.assertEqual(catalog.queries, [])

    def test_catalog_hits_keep_only_titles_that_match_taste(self):
        self._watch()
        catalog = _Catalog({"演示女优": [
            {"number": "EXM-001", "title": "对上的作品", "actors": [{"name": "演示女优", "gender": 0}],
             "maker_name": "演示厂牌", "tags": ["恋足"], "release_date": "2024-06-01", "duration": 120,
             "cover_url": "https://tp.spfcas.com/cover.jpg"},
            {"number": "EXM-002", "title": "无关作品", "actors": [{"name": "另一人", "gender": 0}],
             "maker_name": "其他厂牌", "tags": ["其他"]},
        ]})
        payload = q_taste_board(self.contract, api=catalog)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["source"]["id"], "javdb-public")
        self.assertFalse(payload["source"]["api_key"])
        self.assertEqual([row["code"] for row in payload["recommendations"]], ["EXM-001"])
        self.assertIn("演示女优", payload["recommendations"][0]["reasons"][0])
        self.assertGreaterEqual(payload["recommendations"][0]["taste_score"], 50)
        self.assertTrue(any(row["kind"] == "performer" for row in payload["profile"]["dimensions"]))
        self.assertIn("演示女优", catalog.queries)

    def test_source_failure_keeps_the_profile_and_skips_titles(self):
        self._watch()
        catalog = _Catalog(error=PublicSourceUnavailable("作品资料未取得，请稍后重试"))
        payload = q_taste_board(self.contract, api=catalog)
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["recommendations"], [])
        self.assertTrue(payload["profile"]["dimensions"])
        self.assertTrue(catalog.queries)

    def test_search_terms_prefer_stronger_names_and_cap_each_kind(self):
        strengths = {
            "performer": {"a": 0.9, "b": 0.4, "c": 0.2},
            "tag": {"t": 0.8},
            "studio": {"s": 0.1},
            "series": {},
        }
        names = {
            "performer": {"a": "甲", "b": "乙", "c": "丙"},
            "tag": {"t": "标签"},
            "studio": {"s": "厂牌"},
            "series": {},
        }
        terms = search_terms(strengths, names)
        self.assertEqual([(row["kind"], row["name"]) for row in terms],
                         [("performer", "甲"), ("tag", "标签"), ("performer", "乙")])


if __name__ == "__main__":
    unittest.main()
