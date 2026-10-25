import { NestFactory } from '@nestjs/core';
import { Logger } from '@nestjs/common';
import { configureApp } from '@lark-apaas/fullstack-nestjs-core';
import { join } from 'path';
import { __express as hbsExpressEngine } from 'hbs';
import type { Request, Response, NextFunction, Express } from 'express';
import * as fs from 'fs';
import * as path from 'path';

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

  // V4 终端页面托管：根路径 /、/v4、/v4/* 直接返回 client/public/v4/index.html。
  // 平台 lint 要求 @Controller 路径必须以 api/ 或 openapi/ 开头，不能注册根级 Controller，
  // 因此这里用 express 全局中间件托管页面，行为与原 V4PageController 完全一致：
  // 仅处理 GET 且 Accept 包含 text/html 的页面路由，其余一律 next()，不影响 /api 与静态资源。
  const v4HtmlPath = path.join(process.cwd(), 'client', 'public', 'v4', 'index.html');
  const expressInstance = app.getHttpAdapter().getInstance() as Express;
  expressInstance.use((req: Request, res: Response, next: NextFunction) => {
    const url = (req.url || '/').split('?')[0];
    const isPageRoute = url === '/' || url === '/v4' || url.startsWith('/v4/');
    const accept = req.headers.accept || '';
    if (req.method !== 'GET' || !isPageRoute || !accept.includes('text/html')) {
      return next();
    }
    if (fs.existsSync(v4HtmlPath)) {
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
