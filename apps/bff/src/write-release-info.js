'use strict';
// 构建时写入前端发布身份（FIR-28）。参数缺失或不合法时写入模板（null 字段）→ 页面显示 Version Unknown。
const fs = require('fs');
const RI = require('../../../release_identity.js');

const out = process.argv[2];
const info = {
  schemaVersion: 1,
  version: process.env.RELEASE_VERSION || null,
  commit: process.env.RELEASE_COMMIT || null,
  releasedAt: process.env.RELEASED_AT || null,
  environment: process.env.RELEASE_ENV || null,
  rollbackBaseline: process.env.ROLLBACK_BASELINE || 'v5.0.1',
  repository: 'Firsh3k/kline-visual-pattern-analyzer',
};
const v = RI.validateReleaseInfo(info);
if (!v.ok) console.error(`release-info 不完整（页面将显示 Version Unknown）：${v.errors.join('; ')}`);
fs.writeFileSync(out, JSON.stringify(info, null, 2) + '\n');
