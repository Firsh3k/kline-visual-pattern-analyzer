#!/usr/bin/env bash
# Staging / 生产冒烟测试（只读 + 一次真实分析）。用法：
#   BASE=https://<staging域名> USERNAME=owner PASSWORD='***' CODE=603068 ./deploy/smoke.sh
# 不修改自选股等用户数据。任何一步失败即退出非 0，并打印 requestId 便于排查。
set -euo pipefail
: "${BASE:?需要 BASE}" "${USERNAME:?需要 USERNAME}" "${PASSWORD:?需要 PASSWORD}"
CODE="${CODE:-603068}"
JAR="$(mktemp)"; trap 'rm -f "$JAR"' EXIT
case "$BASE" in https://*) ;; *) echo "BASE 必须是 https://"; exit 2;; esac

step() { printf '\n== %s\n' "$1"; }
step "healthz / readyz"
curl -fsS "$BASE/healthz"; echo
curl -fsS "$BASE/readyz"; echo
step "未登录访问 API 必须 401"
test "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/v1/watchlist")" = "401" && echo ok
step "安全头"
curl -fsSI "$BASE/" | grep -iE '^(strict-transport-security|content-security-policy|x-frame-options):' 
step "登录"
LOGIN=$(curl -fsS -c "$JAR" -H 'content-type: application/json' \
  -d "$(python3 -c 'import json,os;print(json.dumps({"username":os.environ["USERNAME"],"password":os.environ["PASSWORD"]}))')" "$BASE/auth/login")
CSRF=$(printf '%s' "$LOGIN" | python3 -c 'import json,sys;print(json.load(sys.stdin)["csrf"])')
step "版本（网页 / 引擎双身份）"
curl -fsS -b "$JAR" "$BASE/api/v1/version" | python3 -c '
import json,sys; d=json.load(sys.stdin)
print("frontend:", d["frontend"]["version"], d["frontend"]["commit"]); print("engine:", d["engine"].get("version"), d["engine"].get("commit"), d["engine"].get("provider"))
assert d["compatibility"]["ok"], d["compatibility"]'
step "真实分析 $CODE"
curl -fsS -b "$JAR" -H 'content-type: application/json' -H "x-csrf-token: $CSRF" -d "{\"code\":\"$CODE\"}" "$BASE/api/v1/analyses" | python3 -c '
import json,sys; d=json.load(sys.stdin); a=d["analysis"]
print("requestId", d["requestId"]); print(a["name"], a["code"], "asOf", a["as_of"], a["session_state"], "stale", a["calendar"]["stale"])
print("source", a["input"]["provenance"]["source"], a["input"]["adjustment"], "bars", a["input"]["bar_count"], "dataHash", a["input"]["data_hash"])
assert a["input"]["bar_count"] == 250 and a["input"]["provenance"]["mode"] == "live"
print({k: [(r["stage"], r["score"]) for r in v] for k, v in a["core"]["patterns"].items()})'
step "大盘 / 板块 / 校准"
curl -fsS -b "$JAR" "$BASE/api/v1/market" | python3 -c 'import json,sys; m=json.load(sys.stdin)["market_env"]; print(m["as_of"], m["stage"], m["score"])'
curl -fsS -b "$JAR" "$BASE/api/v1/sectors" | python3 -c 'import json,sys; s=json.load(sys.stdin); assert len(s["presets"])==17; print("presets 17, dynamic", len(s["dynamic"]))'
curl -fsS -b "$JAR" "$BASE/api/v1/calibration" | python3 -c 'import json,sys; c=json.load(sys.stdin); print("calibration", c["available"], c["date_range"])'
step "无效代码 fail-closed"
test "$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" -H 'content-type: application/json' -H "x-csrf-token: $CSRF" -d '{"code":"12345"}' "$BASE/api/v1/analyses")" = "422" && echo ok
printf '\n全部通过\n'
