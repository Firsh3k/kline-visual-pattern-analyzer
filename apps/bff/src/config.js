'use strict';
/**
 * BFF 配置：只从环境变量读取。密钥（Python 服务凭证、会话密钥、密码哈希）只在服务端，
 * 不进入浏览器包、日志或 URL。
 */
const fs = require('fs');
const path = require('path');

class ConfigError extends Error {}

function required(env, name, minLen = 1) {
  const v = env[name];
  if (!v || v.length < minLen) {
    throw new ConfigError(`${name} 必须设置${minLen > 1 ? `（至少 ${minLen} 个字符）` : ''}`);
  }
  return v;
}

function int(env, name, def, lo, hi) {
  const raw = env[name];
  if (raw === undefined || raw === '') return def;
  const v = Number(raw);
  if (!Number.isInteger(v) || v < lo || v > hi) throw new ConfigError(`${name} 必须是 ${lo}..${hi} 的整数`);
  return v;
}

function loadConfig(env = process.env) {
  const nodeEnv = env.NODE_ENV || 'production';
  const deployEnv = env.DEPLOY_ENV || (nodeEnv === 'development' ? 'development' : 'staging');
  if (!['development', 'staging', 'production'].includes(deployEnv)) {
    throw new ConfigError('DEPLOY_ENV 只能是 development / staging / production');
  }
  const base = required(env, 'PYTHON_API_BASE_URL');
  let url;
  try { url = new URL(base); } catch (e) { throw new ConfigError('PYTHON_API_BASE_URL 不是合法 URL'); }
  const privateNet = env.PYTHON_API_PRIVATE_NETWORK === 'true';
  const isLocal = ['localhost', '127.0.0.1', '::1'].includes(url.hostname);
  if (url.protocol === 'http:' && !(privateNet || (isLocal && deployEnv === 'development'))) {
    // 正常 TLS：公网路径必须 https；http 只允许显式声明的私有网络（如 docker compose 内网）
    throw new ConfigError('PYTHON_API_BASE_URL 必须是 https://（仅私有网络可设置 PYTHON_API_PRIVATE_NETWORK=true 使用 http）');
  }
  if (!['http:', 'https:'].includes(url.protocol)) throw new ConfigError('PYTHON_API_BASE_URL 协议不支持');
  if (env.NODE_TLS_REJECT_UNAUTHORIZED === '0') {
    throw new ConfigError('禁止设置 NODE_TLS_REJECT_UNAUTHORIZED=0（不得关闭 TLS 校验）');
  }
  const passwordHash = required(env, 'LOGIN_PASSWORD_HASH');
  if (!/^scrypt\$\d+\$\d+\$\d+\$[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+$/.test(passwordHash)) {
    throw new ConfigError('LOGIN_PASSWORD_HASH 格式应为 scrypt$N$r$p$salt$hash（用 npm run hash-password 生成）');
  }
  const cookieSecure = env.COOKIE_SECURE ? env.COOKIE_SECURE === 'true' : deployEnv !== 'development';
  if (!cookieSecure && deployEnv !== 'development') throw new ConfigError('非开发环境 Cookie 必须 Secure');

  // 跨域访问（如妙搭托管的网页）：只允许列出的来源，用 Authorization: Bearer 令牌访问，不用 Cookie
  const corsOrigins = String(env.CORS_ALLOWED_ORIGINS || '').split(',').map((o) => o.trim()).filter(Boolean);
  for (const o of corsOrigins) {
    let u;
    try { u = new URL(o); } catch (e) { throw new ConfigError(`CORS_ALLOWED_ORIGINS 里的 ${o} 不是合法来源`); }
    const localDev = u.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(u.hostname) && deployEnv === 'development';
    if ((u.protocol !== 'https:' && !localDev) || u.origin !== o) {
      throw new ConfigError(`CORS_ALLOWED_ORIGINS 里的 ${o} 必须是 https://域名 形式（不带路径和结尾斜杠）`);
    }
  }

  const releaseFile = env.FRONTEND_RELEASE_FILE || path.resolve(__dirname, '../../../release-info.json');
  let releaseInfo = null;
  try { releaseInfo = JSON.parse(fs.readFileSync(releaseFile, 'utf8')); } catch (e) { releaseInfo = null; }

  return {
    port: int(env, 'PORT', 8080, 1, 65535),
    host: env.BIND_HOST || undefined,           // 默认监听所有网卡；本机运行时可设 127.0.0.1
    deployEnv,
    pythonBase: url.toString().replace(/\/$/, ''),
    pythonToken: required(env, 'PYTHON_API_TOKEN', 32),
    sessionSecret: required(env, 'SESSION_SECRET', 32),
    loginUsername: required(env, 'LOGIN_USERNAME'),
    passwordHash,
    userId: env.SESSION_USER_ID || 'owner',
    sessionTtlS: int(env, 'SESSION_TTL_S', 7 * 24 * 3600, 300, 30 * 24 * 3600),
    cookieSecure,
    corsOrigins,
    trustProxy: int(env, 'TRUST_PROXY_HOPS', 1, 0, 5),
    timeouts: {
      connectAndDefault: int(env, 'UPSTREAM_TIMEOUT_MS', 8000, 500, 60000),
      analysis: int(env, 'UPSTREAM_ANALYSIS_TIMEOUT_MS', 30000, 1000, 120000),
      ready: 3000,
    },
    upstreamConcurrency: int(env, 'UPSTREAM_CONCURRENCY', 16, 1, 128),
    rate: {
      apiPerMin: int(env, 'RATE_API_PER_MIN', 240, 10, 10000),
      analysisPerMin: int(env, 'RATE_ANALYSIS_PER_MIN', 60, 1, 1000),
      scanCreatePerMin: int(env, 'RATE_SCAN_CREATE_PER_MIN', 6, 1, 100),
      loginPer15Min: int(env, 'RATE_LOGIN_PER_15MIN', 10, 1, 100),
    },
    expectedSchemas: { api: '1.0', analysis: '3.0' },
    releaseInfo,
    staticDir: env.STATIC_DIR || path.resolve(__dirname, '../../web'),
    staticDirV5: env.STATIC_DIR_V5 || path.resolve(__dirname, '../../web-v5'),
  };
}

module.exports = { loadConfig, ConfigError };
