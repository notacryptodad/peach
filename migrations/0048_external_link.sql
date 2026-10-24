-- 外部链接：当前使用者的一份页面清单。作品行只保存地址和元数据。
--
-- 媒体文件留在原地址。`asset.location='external'`、`asset.medium='link'`、`asset.path` 是
-- 规范化后的 HTTPS 页面地址，扫描和视频处理不领取这一来源。
-- 同一个地址全库一条 asset，清单行按 profile 分开。标星仍走 asset_preference，标签仍走
-- asset_tag / asset_entity。
CREATE TABLE external_link(
  id INTEGER PRIMARY KEY,
  profile_id TEXT NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
  asset_id INTEGER NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
  url TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  site TEXT NOT NULL DEFAULT '',
  thumbnail_url TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(profile_id, url)
);

CREATE INDEX idx_external_link_profile ON external_link(profile_id, updated_at DESC);
CREATE INDEX idx_external_link_asset ON external_link(asset_id);

INSERT INTO ledger_revision(tbl) VALUES('external_link');

CREATE TRIGGER rev_external_link_insert AFTER INSERT ON external_link BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='external_link';
END;

CREATE TRIGGER rev_external_link_update AFTER UPDATE ON external_link BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='external_link';
END;

CREATE TRIGGER rev_external_link_delete AFTER DELETE ON external_link BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='external_link';
END;
