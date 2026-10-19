/**
 * 防回潮：实际部署的 V4 页面里不得再出现本地 JS 形态引擎 / 推荐度 / 成功率 / 本地个股取数 / JS 降级入口。
 * 这些函数已在清理 PR 中删除；任何一个重新出现都应在 CI 失败，而不是悄悄被接回正式路径。
 *
 * 运行：node tests/v6/test_v4_no_local_engine.js
 */
'use strict';
const assert = require('assert');
const { loadPage } = require('./v4_page_harness');

const FORBIDDEN = [
  'matchPatterns', 'matchBearPatterns', 'matchWeeklyPatterns',   // 本地形态识别
  'calcAttentionScore',                                           // 综合推荐度
  'calcEnvAccuracy', 'PATTERN_ENV_ACCURACY',                      // 环境适配“准确率”
  'calcNecklineConfirm',                                          // JS 颈线确认
  'convertPythonPatternToV4', 'mergePythonPatterns',              // 本地 / Python 形态混合
  'fetchPythonPatterns', 'validateApiSchema',                     // 旧 Python 接口路径
  'fetchKline', 'fetchStockNameJSONP',                            // 个股本地取数
  'forceJsAnalyze',                                               // JS 降级入口
];

const p = loadPage({ exports: FORBIDDEN });
const present = FORBIDDEN.filter(n => p.x[n] !== undefined);
assert.deepStrictEqual(present, [], '页面重新出现了已删除的本地引擎函数：' + present.join(', '));
console.log(`OK：${FORBIDDEN.length} 个本地引擎 / 推荐度 / 降级函数均不存在`);
