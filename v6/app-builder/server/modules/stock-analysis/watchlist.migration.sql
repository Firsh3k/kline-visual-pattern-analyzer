-- FIR-42：自选股（全站共用一份）。由平台 migration 执行；应用运行时只做 DML，不建表。
-- 应用账号需要：两张表的 SELECT, INSERT, UPDATE（不需要 DELETE / CREATE）。可重复执行。
CREATE TABLE IF NOT EXISTS kline_watchlist (
  code        text PRIMARY KEY CHECK (code ~ '^[0-9]{6}$'),
  state       text NOT NULL CHECK (state IN ('active', 'deleted')),
  added_at    timestamptz NOT NULL,
  deleted_at  timestamptz,
  updated_at  timestamptz NOT NULL,
  rev         bigint NOT NULL
);
CREATE TABLE IF NOT EXISTS kline_watchlist_meta (
  id   int PRIMARY KEY CHECK (id = 1),
  rev  bigint NOT NULL
);
-- 版本行缺失时按已有数据的最大 rev 补上（空表为 0），不覆盖、不降低已存在的版本（QA R76-1）
INSERT INTO kline_watchlist_meta (id, rev)
SELECT 1, COALESCE(MAX(rev), 0) FROM kline_watchlist
ON CONFLICT (id) DO NOTHING;
