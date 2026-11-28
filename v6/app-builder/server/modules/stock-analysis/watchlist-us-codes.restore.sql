-- FIR-48 恢复脚本：watchlist-us-codes.rollback.sql 的逆向。
-- 仅在已重新部署放开双市场的代码路径后执行；由平台 migration 账号执行。可重复执行（幂等）。
--
-- 步骤：先重新放开双市场 CHECK，再从 fir48_backup_* 备份表回载美股行（ON CONFLICT DO NOTHING）。
-- 墓碑记录保留在备份表中，需要恢复删除记录时由应用层按备份表内容重新迁入。
BEGIN;

-- ---------- 1. 重新放开双市场 CHECK（动态定位 code 列 CHECK，不依赖默认约束名） ----------
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
  ADD CONSTRAINT kline_watchlist_code_check
  CHECK (code ~ '^[0-9]{6}$' OR code ~ '^[A-Z]{1,6}(\.[A-Z])?$');

-- ---------- 2. 回载备份的美股自选（已存在则不覆盖） ----------
INSERT INTO kline_watchlist (code, state, added_at, deleted_at, updated_at, rev)
SELECT code, state, added_at, deleted_at, updated_at, rev
FROM fir48_backup_watchlist
WHERE source = 'us'
ON CONFLICT (code) DO NOTHING;

-- ---------- 3. 回载备份的含美股成分板块（已存在则不覆盖） ----------
INSERT INTO kline_custom_sectors (name, codes_json, state, created_at, updated_at, deleted_at, rev)
SELECT name, codes_json, state, created_at, updated_at, deleted_at, rev
FROM fir48_backup_sectors
WHERE source = 'us'
ON CONFLICT (name) DO NOTHING;

COMMIT;
