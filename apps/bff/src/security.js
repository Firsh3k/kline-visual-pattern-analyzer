'use strict';
/**
 * 会话（HMAC 签名的无状态 Cookie）、scrypt 密码校验、CSRF（双提交 + 与会话绑定）、限流、安全响应头。
 */
const crypto = require('crypto');

const SESSION_COOKIE = 'kl_session';
const CSRF_COOKIE = 'kl_csrf';

function b64url(buf) { return Buffer.from(buf).toString('base64url'); }

function hmac(secret, data) { return crypto.createHmac('sha256', secret).update(data).digest('base64url'); }

function safeEqual(a, b) {
  const ba = Buffer.from(String(a)); const bb = Buffer.from(String(b));
  return ba.length === bb.length && crypto.timingSafeEqual(ba, bb);
}

// ------------------------------------------------------------------ 密码
function hashPassword(password, { N = 16384, r = 8, p = 1 } = {}) {
  const salt = crypto.randomBytes(16);
  const hash = crypto.scryptSync(password, salt, 32, { N, r, p, maxmem: 64 * 1024 * 1024 });
  return `scrypt$${N}$${r}$${p}$${salt.toString('base64')}$${hash.toString('base64')}`;
}

function verifyPassword(password, stored) {
  const [alg, N, r, p, salt, hash] = String(stored).split('$');
  if (alg !== 'scrypt') return false;
  const expected = Buffer.from(hash, 'base64');
  const got = crypto.scryptSync(String(password), Buffer.from(salt, 'base64'), expected.length,
    { N: Number(N), r: Number(r), p: Number(p), maxmem: 64 * 1024 * 1024 });
  return got.length === expected.length && crypto.timingSafeEqual(got, expected);
}

// ------------------------------------------------------------------ 会话
function issueSession(secret, userId, ttlS, now = Date.now()) {
  const payload = { u: userId, sid: b64url(crypto.randomBytes(12)), csrf: b64url(crypto.randomBytes(24)),
    iat: Math.floor(now / 1000), exp: Math.floor(now / 1000) + ttlS };
  const body = b64url(JSON.stringify(payload));
  return { token: `${body}.${hmac(secret, body)}`, payload };
}

function readSession(secret, token, now = Date.now()) {
  if (typeof token !== 'string' || token.length > 2048) return null;
  const [body, sig] = token.split('.');
  if (!body || !sig || !safeEqual(sig, hmac(secret, body))) return null;
  let p;
  try { p = JSON.parse(Buffer.from(body, 'base64url').toString('utf8')); } catch (e) { return null; }
  if (!p || typeof p.exp !== 'number' || p.exp * 1000 < now) return null;
  return p;
}

function parseCookies(header) {
  const out = {};
  for (const part of String(header || '').split(';')) {
    const i = part.indexOf('=');
    if (i > 0) out[part.slice(0, i).trim()] = decodeURIComponent(part.slice(i + 1).trim());
  }
  return out;
}

function cookie(name, value, { maxAge, httpOnly = true, secure = true, sameSite = 'Strict' } = {}) {
  const parts = [`${name}=${encodeURIComponent(value)}`, 'Path=/', `SameSite=${sameSite}`];
  if (maxAge !== undefined) parts.push(`Max-Age=${maxAge}`);
  if (httpOnly) parts.push('HttpOnly');
  if (secure) parts.push('Secure');
  return parts.join('; ');
}

// ------------------------------------------------------------------ 限流（固定窗口，内存；单实例部署）
class RateLimiter {
  constructor(limit, windowMs) { this.limit = limit; this.windowMs = windowMs; this.buckets = new Map(); }
  hit(key, now = Date.now()) {
    let b = this.buckets.get(key);
    if (!b || now - b.start >= this.windowMs) { b = { start: now, n: 0 }; this.buckets.set(key, b); }
    b.n += 1;
    if (this.buckets.size > 10000) {
      for (const [k, v] of this.buckets) if (now - v.start >= this.windowMs) this.buckets.delete(k);
    }
    return { ok: b.n <= this.limit, retryAfterS: Math.ceil((b.start + this.windowMs - now) / 1000) };
  }
}

// ------------------------------------------------------------------ 安全头
function securityHeaders(secure) {
  return (req, res, next) => {
    res.setHeader('Content-Security-Policy',
      "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; " +
      "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'");
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('X-Frame-Options', 'DENY');
    res.setHeader('Permissions-Policy', 'camera=(), microphone=(), geolocation=()');
    res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
    if (secure) res.setHeader('Strict-Transport-Security', 'max-age=31536000; includeSubDomains');
    next();
  };
}

module.exports = {
  SESSION_COOKIE, CSRF_COOKIE, hashPassword, verifyPassword, issueSession, readSession, parseCookies, cookie,
  RateLimiter, securityHeaders, safeEqual,
};
