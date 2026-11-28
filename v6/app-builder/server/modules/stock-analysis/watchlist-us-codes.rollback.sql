-- FIR-48 回退脚本：恢复 v6-web-1.2.0 兼容的「仅 A 股六位数字」状态。
-- 由平台 migration 账号执行；应用账号无 DDL / DELETE 权限。可重复执行（幂等）。
--
-- 设计约束（与迁移演练口径一致）：
--   * 非破坏：美股自选 / 含美股成分的板块 / 删除记录先整行复制到备份表，再做谓词级删除；
--   * 不使用 TRUNCATE、不清空清单；A 股行一行不动；DELETE 全部带明确 WHERE 谓词；
--   * 备份不去重覆盖已存在条目；回退后主表恢复六位数字 CHECK，旧版可正常插入并分析 A 股。
--
-- 配套恢复：watchlist-us-codes.restore.sql（从备份表逆向回载美股行）。
BEGIN;

-- ---------- 1. 备份表（与主表同构 + 元数据列） ----------
CREATE TABLE IF NOT EXISTS fir48_backup_watchlist (
  backup_id    bigint GENERATED ALWAYS AS IDENTITY,
  backed_up_at timestamptz NOT NULL DEFAULT now(),
  source       text NOT NULL,              -- 'us' = 美股自选; 'tombstone' = 删除记录
  code         text,
  state        text,
  added_at     timestamptz,
  deleted_at   timestamptz,
  updated_at   timestamptz,
  rev          bigint
);

CREATE TABLE IF NOT EXISTS fir48_backup_sectors (
  backup_id    bigint GENERATED ALWAYS AS IDENTITY,
  backed_up_at timestamptz NOT NULL DEFAULT now(),
  source       text NOT NULL,              -- 'us' = 含美股成分; 'tombstone' = 板块删除记录
  name         text,
  codes_json   text,
  state        text,
  created_at   timestamptz,
  updated_at   timestamptz,
  deleted_at   timestamptz,
  rev          bigint
);

-- ---------- 2a. 备份美股自选（非六位数字代码） ----------
INSERT INTO fir48_backup_watchlist
  (source, code, state, added_at, deleted_at, updated_at, rev)
SELECT 'us', w.code, w.state, w.added_at, w.deleted_at, w.updated_at, w.rev
FROM kline_watchlist w
WHERE w.code !~ '^[0-9]{6}$'
  AND NOT EXISTS (
    SELECT 1 FROM fir48_backup_watchlist b
    WHERE b.source = 'us' AND b.code = w.code
  );

-- ---------- 2b. 备份自选墓碑（删除记录，防止回退后丢失） ----------
INSERT INTO fir48_backup_watchlist
  (source, code, state, added_at, deleted_at, updated_at, rev)
SELECT 'tombstone', w.code, w.state, w.added_at, w.deleted_at, w.updated_at, w.rev
FROM kline_watchlist w
WHERE w.state = 'deleted'
  AND NOT EXISTS (
    SELECT 1 FROM fir48_backup_watchlist b
    WHERE b.source = 'tombstone' AND b.code = w.code
  );

-- ---------- 2c. 备份含美股成分的板块 ----------
INSERT INTO fir48_backup_sectors
  (source, name, codes_json, state, created_at, updated_at, deleted_at, rev)
SELECT 'us', c.name, c.codes_json, c.state, c.created_at, c.updated_at, c.deleted_at, c.rev
FROM kline_custom_sectors c
WHERE EXISTS (
  SELECT 1 FROM json_array_elements_text(c.codes_json::json) e
  WHERE e !~ '^[0-9]{6}$'
)
AND NOT EXISTS (
  SELECT 1 FROM fir48_backup_sectors b
  WHERE b.source = 'us' AND b.name = c.name
);

-- ---------- 2d. 备份板块墓碑 ----------
INSERT INTO fir48_backup_sectors
  (source, name, codes_json, state, created_at, updated_at, deleted_at, rev)
SELECT 'tombstone', c.name, c.codes_json, c.state, c.created_at, c.updated_at, c.deleted_at, c.rev
FROM kline_custom_sectors c
WHERE c.state = 'deleted'
  AND NOT EXISTS (
    SELECT 1 FROM fir48_backup_sectors b
    WHERE b.source = 'tombstone' AND b.name = c.name
  );

-- ---------- 3. 谓词级移除主表美股行（仅非六位数字；A 股不动） ----------
DELETE FROM kline_watchlist WHERE code !~ '^[0-9]{6}$';

DELETE FROM kline_custom_sectors c
WHERE EXISTS (
  SELECT 1 FROM json_array_elements_text(c.codes_json::json) e
  WHERE e !~ '^[0-9]{6}$'
);

-- ---------- 4. 恢复基线六位数字 CHECK（按 pg_constraint 动态定位 code 列 CHECK） ----------
DO $$
DECLARE
  cname text;
BEGIN
  FOR cname IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = 'kline_watchlist'::regclass
      AND contype = 'c'
      AND conkey @> ARRAY[
        (SELECT attnum FROM pg_attribute
          WHERE attrelid = 'kline_watchlist'::regclass AND attname = 'code')
      ]
  LOOP
    EXECUTE format('ALTER TABLE kline_watchlist DROP CONSTRAINT %I', cname);
  END LOOP;
END $$;

ALTER TABLE kline_watchlist
  ADD CONSTRAINT kline_watchlist_code_check CHECK (code ~ '^[0-9]{6}$');

COMMIT;
