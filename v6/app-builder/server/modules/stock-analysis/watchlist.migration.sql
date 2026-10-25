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
INSERT INTO kline_watchlist_meta (id, rev) VALUES (1, 0) ON CONFLICT (id) DO NOTHING;
