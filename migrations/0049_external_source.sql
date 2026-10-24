-- 片源站点：当前使用者允许从哪些站点页面读取播放地址。
--
-- 只保存域名。播放时再读页面里写明的媒体地址，媒体文件不写入本机。
CREATE TABLE external_source(
  id INTEGER PRIMARY KEY,
  profile_id TEXT NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
  host TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(profile_id, host)
);

INSERT INTO ledger_revision(tbl) VALUES('external_source');

CREATE TRIGGER rev_external_source_insert AFTER INSERT ON external_source BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='external_source';
END;

CREATE TRIGGER rev_external_source_update AFTER UPDATE ON external_source BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='external_source';
END;

CREATE TRIGGER rev_external_source_delete AFTER DELETE ON external_source BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='external_source';
END;
