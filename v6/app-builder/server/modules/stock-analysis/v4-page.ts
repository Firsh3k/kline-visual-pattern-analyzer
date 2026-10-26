/**
 * V4 页面托管（根路径 /、/v4、/v4/*）。
 *
 * 平台 commit_task 要求每个 @Controller 的路径以 api/ 或 openapi/ 开头，原来的根级 V4PageController
 * 提交不了（2026-10-25 发布受阻），所以改由 server/main.ts 注册这个 express 中间件：
 * - 只处理 GET 且 Accept 含 text/html 的页面路由，其余一律 next()，不影响 /api 与静态资源；
 * - 平台把应用挂在子路径（CLIENT_BASE_PATH，形如 /app/<appId>）下，原生中间件拿到的 url 带这个前缀，
 *   判断前先去掉（只用于判断，不改写 req.url）；
 * - 返回的文件与 /api/health 的 deploy.page_sha256 是同一个（v4PagePath）。
 * 无 Nest 依赖，tests/v6/test_repo_equals_prod.js 直接测试。
 */
import type { NextFunction, Request, Response } from 'express';
import * as fs from 'fs';
import { v4PagePath } from './deploy-info';

const PLATFORM_PREFIX = /^\/app\/[^/]+(?=\/|$)/;

/** 去掉平台子路径前缀与查询串后的路由 */
export function pageRoute(url: string, basePath: string | undefined = process.env.CLIENT_BASE_PATH): string {
  let route = (url || '/').split('?')[0];
  const base = (basePath || '').replace(/\/+$/, '');
  if (base && (route === base || route.startsWith(base + '/'))) {
    route = route.slice(base.length) || '/';
  } else {
    const m = route.match(PLATFORM_PREFIX);
    if (m) route = route.slice(m[0].length) || '/';
  }
  return route;
}

export function isV4PageRequest(method: string, url: string, accept: string | undefined, basePath?: string): boolean {
  if (method !== 'GET' || !(accept || '').includes('text/html')) return false;
  const route = pageRoute(url, basePath);
  return route === '/' || route === '/v4' || route.startsWith('/v4/');
}

export function createV4PageMiddleware(resolvePage: () => string | null = () => v4PagePath()) {
  return (req: Request, res: Response, next: NextFunction): void => {
    if (!isV4PageRequest(req.method, req.url, req.headers.accept)) {
      next();
      return;
    }
    const file = resolvePage();
    if (!file) {
      res.status(404).send('V4 page not found');
      return;
    }
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    res.setHeader('Cache-Control', 'no-cache');
    fs.createReadStream(file).pipe(res);
  };
}
