-- 关注演员：当前使用者关心的演员名。
--
-- 收录时按这个名字向公开目录取一页作品，只写入页面地址和元数据。
CREATE TABLE external_actor(
  id INTEGER PRIMARY KEY,
  profile_id TEXT NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(profile_id, name)
);

INSERT INTO ledger_revision(tbl) VALUES('external_actor');

CREATE TRIGGER rev_external_actor_insert AFTER INSERT ON external_actor BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='external_actor';
END;

CREATE TRIGGER rev_external_actor_update AFTER UPDATE ON external_actor BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='external_actor';
END;

CREATE TRIGGER rev_external_actor_delete AFTER DELETE ON external_actor BEGIN
  UPDATE ledger_revision SET n=n+1 WHERE tbl='external_actor';
END;
