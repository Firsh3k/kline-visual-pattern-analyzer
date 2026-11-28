-- FIR-48：自选股代码放开为「A 股 6 位数字 + 已登记美股字母代码」。
-- 由平台 migration 账号执行（应用账号只有 SELECT / INSERT / UPDATE，无 DDL 权限）；可重复执行（幂等）。
--
-- 性质：非破坏迁移。只替换 kline_watchlist.code 列的 CHECK 约束，不改写任何数据行：
--   - 旧 A 股行、删除记录（墓碑）、行顺序、rev 版本号全部原样保留；
--   - 不新增 / 删除列，不重写表，不动应用账号权限；
--   - 约束口径与 BFF stock-analysis.pure.ts 的 assertCode / Python core.market_data.market 对齐：
--       A 股：6 位数字（如 600519）；美股：1-6 位大写字母，可带 .X 后缀（如 AAPL、BRK.B）。
--
-- 自定义板块表 kline_custom_sectors 的成分存 codes_json（文本数组），code 维度没有独立 CHECK 约束，
-- 格式 / 已登记校验在 BFF 与 Python 层完成，本迁移无需改动该表；历史板块成分、墓碑、顺序、rev 全部保留。
--
-- 分层口径（避免误读）：
--   - DB 层 CHECK 只接受【大写】字母；小写 / 带空格的代码（如 aapl、" AAPL "）由 BFF normalizeCode
--     去空格 + 转大写后入库，DB 不负责归一化；
--   - 格式合法但【未登记】的美股（如 ZZZZ）DB 层会放行，是否可取数由应用层 US_EXCHANGE_MAP
--     （与 Python core.market_data.market 同源）拦截。

BEGIN;

-- 1) 去掉 code 列上现存的 CHECK 约束。不依赖 PostgreSQL 默认约束名（<表>_<列>_check）：
--    按 pg_constraint 找 conkey 指向 code 列的 CHECK，逐个动态 DROP；主键 / state 检查不受影响。
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

-- 2) 换上双市场 CHECK。重复执行时步骤 1 先清掉旧约束（含本次新建的），不会产生重复约束。
ALTER TABLE kline_watchlist
  ADD CONSTRAINT kline_watchlist_code_check
  CHECK (code ~ '^[0-9]{6}$' OR code ~ '^[A-Z]{1,6}(\.[A-Z])?$');

COMMIT;

-- ---------------------------------------------------------------------------
-- 回退 / 恢复（仅当需要恢复 v6-web-1.2.0 旧口径时，由平台 migration 账号执行）：
--   - 回退：watchlist-us-codes.rollback.sql
--       先把美股自选 / 含美股成分的板块 / 墓碑整行复制到备份表（谓词级 INSERT，不去重覆盖已存在条目），
--       再谓词级 DELETE 主表美股行（A 股一行不动，无 TRUNCATE），最后恢复旧的六位数字 CHECK。
--   - 恢复：watchlist-us-codes.restore.sql
--       重新放开双市场 CHECK，再从备份表逆向回载美股行（ON CONFLICT DO NOTHING，幂等）。
-- 回退前请先确认备份表已生成；旧版恢复运行后，美股自选 / 板块成分从备份表（或前端导出 JSON）恢复。
