/**
 * CI 复现妙搭 commit_task 的服务端 ESLint（严格模式）：@lark-apaas/fullstack-presets 的 eslintPresetsOfSimple.server，
 * 与平台 eslint.config.js 用的是同一份预设（开发 2026-10-26 回写 FIR-41）。只检查 server/。
 * 平台的 eslint.config.js 不在仓库里（平台生成），所以这份配置放在 tests/，不放进 app 目录，避免同步时覆盖平台文件。
 *
 * 预设与插件从 v6/app-builder/node_modules 加载（CI 先 npm ci），由 tests/v6/test_platform_lint.js 调用。
 */
'use strict';
const path = require('path');
const { createRequire } = require('module');

const APP = path.resolve(__dirname, '..', '..', 'v6', 'app-builder');
const appRequire = createRequire(path.join(APP, 'package.json'));
const { eslintPresetsOfSimple } = appRequire('@lark-apaas/fullstack-presets');

module.exports = [
  { ignores: ['dist/**', 'node_modules/**', 'client/**', 'scripts/**', 'shared/**', '*.config.*'] },
  ...eslintPresetsOfSimple.server.map((c) => (Object.keys(c).length === 1 && c.ignores ? c : { ...c, files: ['server/**/*.ts'] })),
  // 预设里 @darraghor/nestjs-typed 的部分规则需要类型信息，平台配置同样要给 parserOptions（用 server 的 tsconfig）
  {
    files: ['server/**/*.ts'],
    languageOptions: { parserOptions: { project: path.join(APP, 'tsconfig.node.json'), tsconfigRootDir: APP } },
  },
];
