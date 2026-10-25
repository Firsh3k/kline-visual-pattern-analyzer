import { Controller, Get, Res, Req } from '@nestjs/common';
import type { Response, Request } from 'express';
import * as fs from 'fs';
import { v4PagePath } from './deploy-info';

@Controller()
export class V4PageController {
  @Get('/')
  serveV4(@Req() req: Request, @Res() res: Response) {
    const accept = req.headers.accept || '';
    if (!accept.includes('text/html')) {
      res.status(404).send('Not Found');
      return;
    }
    const v4Path = v4PagePath();   // 与 /api/health 的 deploy.page_sha256 同一个文件
    if (fs.existsSync(v4Path)) {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      res.setHeader('Cache-Control', 'no-cache');
      fs.createReadStream(v4Path).pipe(res);
    } else {
      res.status(404).send('V4 page not found');
    }
  }
}
