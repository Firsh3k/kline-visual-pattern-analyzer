import { NestFactory } from '@nestjs/core';
import { Logger } from '@nestjs/common';
import { configureApp } from '@lark-apaas/fullstack-nestjs-core';
import { join } from 'path';
import { __express as hbsExpressEngine } from 'hbs';
import type { Request, Response, NextFunction, Express } from 'express';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

import type { NestExpressApplication } from '@nestjs/platform-express';
import { AppModule } from './app.module';

async function bootstrap() {
  const app = await NestFactory.create<NestExpressApplication>(AppModule, {
    abortOnError: process.env.NODE_ENV !== 'development',
  });
  await configureApp(app, {
    disableSwagger: true,
  });
  const logger = new Logger('Bootstrap');
  const host = process.env.SERVER_HOST || 'localhost';
  const port = Number(process.env.SERVER_PORT || '3000');

  // 注册视图引擎, 渲染 client 目录下的 html 文件
  app.setBaseViewsDir(join(process.cwd(), 'dist/client'));
  app.setViewEngine('html');
  app.engine('html', hbsExpressEngine);

  // V4 终端页面托管（根路径 /、/v4、/v4/* 返回 client/public/v4/index.html）。
  // 平台 lint 要求 @Controller 路径必须以 api/ 或 openapi/ 开头，不能注册根级 Controller，
  // 故改用原生 express 全局中间件；仅处理 GET 且 Accept 含 text/html 的页面路由，其余 next()。
  //
  // 两个平台细节：
  // 1) fullstack 应用部署在子路径 /app/<appId>/ 下，NestJS 控制器由平台路由层处理前缀，
  //    原生 express 中间件拿到的是带前缀的 req.url，需要自行剥离（只用于判断，不改写 req.url，
  //    避免影响后续 NestJS 的 /api 路由）。
  // 2) 兼容两种文件布局：
  //    - 标准云端构建 prod（cwd=dist）：build.sh 把 client/public 归集到 dist/dist/client/v4，
  //      即 process.cwd()/dist/client/v4/index.html；
  //    - 开发 / 直接 rsync：process.cwd()/client/public/v4/index.html。
  const v4HtmlCandidates = [
    path.join(process.cwd(), 'dist', 'client', 'v4', 'index.html'),
    path.join(process.cwd(), 'client', 'public', 'v4', 'index.html'),
  ];
  const resolveV4Html = (): string | null =>
    v4HtmlCandidates.find((p) => fs.existsSync(p)) ?? null;

  const expressInstance = app.getHttpAdapter().getInstance() as Express;

  // 临时诊断：列出运行时真实文件树与扩展名统计，定位 .py 是否进入运行时（用完即删）。
  expressInstance.use((req: Request, res: Response, next: NextFunction) => {
    let route = (req.url || '/').split('?')[0];
    const pm = route.match(/^\/app\/[^/]+(?=\/|$)/);
    if (pm) route = route.slice(pm[0].length) || '/';
    if (req.method === 'GET' && route === '/api/_diag/files') {
      const cwd = process.cwd();
      const probe = (p: string) => {
        const full = path.resolve(p);
        try {
          const s = fs.statSync(full);
          return { exists: true, dir: s.isDirectory(), file: s.isFile(), symlink: fs.lstatSync(full).isSymbolicLink(), size: s.size };
        } catch (e) {
          return { exists: false, error: e instanceof Error ? e.message : String(e) };
        }
      };
      // 1) engine-bundle 候选路径是否存在
      const candidates = [
        'server/engine-bundle.js',
        'engine-bundle.js',
        'dist/server/engine-bundle.js',
        path.resolve(__dirname, '..', '..', 'engine-bundle.js'),
        'server/modules/stock-analysis/stock-analysis.service.js',
      ];
      const checks: Record<string, unknown> = {};
      for (const c of candidates) checks[c] = probe(c);
      // 2) server 顶层（含 symlink 判断）
      let serverTop: unknown = [];
      let serverInfo: unknown = null;
      try {
        serverInfo = probe(path.join(cwd, 'server'));
        serverTop = fs.readdirSync(path.join(cwd, 'server'));
      } catch (e) {
        serverTop = 'ERR:' + (e instanceof Error ? e.message : String(e));
      }
      // 3) 临时目录可写性
      let tmpCheck: unknown;
      try {
        const tp = path.join(os.tmpdir(), 'kline_write_test');
        fs.writeFileSync(tp, 'ok');
        fs.unlinkSync(tp);
        tmpCheck = { tmpdir: os.tmpdir(), writable: true };
      } catch (e) {
        tmpCheck = { tmpdir: os.tmpdir(), writable: false, error: e instanceof Error ? e.message : String(e) };
      }
      // 4) 尝试 require bundle
      let requireCheck: unknown;
      try {
        const bp = path.resolve(__dirname, '..', '..', 'engine-bundle.js');
        if (fs.existsSync(bp)) {
          // eslint-disable-next-line @typescript-eslint/no-require-imports
          const m = require(bp) as { fileCount?: number; payload?: string };
          requireCheck = { ok: true, bundlePath: bp, fileCount: m.fileCount, payloadLen: (m.payload || '').length };
        } else {
          requireCheck = { ok: false, reason: 'bundle not found at ' + bp };
        }
      } catch (e) {
        requireCheck = { ok: false, error: e instanceof Error ? e.message : String(e) };
      }
      res.json({ cwd, dirname: __dirname, serverInfo, serverTop, checks, tmpCheck, requireCheck });
      return;
    }
    next();
  });

  expressInstance.use((req: Request, res: Response, next: NextFunction) => {
    const accept = req.headers.accept || '';
    if (req.method !== 'GET' || !accept.includes('text/html')) {
      return next();
    }
    // 剥离平台子路径前缀 /app/<appId>（仅用于判断页面路由，不改写 req.url）
    let route = (req.url || '/').split('?')[0];
    const prefixMatch = route.match(/^\/app\/[^/]+(?=\/|$)/);
    if (prefixMatch) {
      route = route.slice(prefixMatch[0].length) || '/';
    }
    const isPageRoute = route === '/' || route === '/v4' || route.startsWith('/v4/');
    if (!isPageRoute) {
      return next();
    }
    const v4HtmlPath = resolveV4Html();
    if (v4HtmlPath) {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      res.setHeader('Cache-Control', 'no-cache');
      return fs.createReadStream(v4HtmlPath).pipe(res);
    }
    return res.status(404).send('V4 page not found');
  });

  await app.listen(port, host);
  logger.log(`Server running on ${host}:${port}`);
  logger.log(`API endpoints ready at http://${host}:${port}/api`);
}

bootstrap();
