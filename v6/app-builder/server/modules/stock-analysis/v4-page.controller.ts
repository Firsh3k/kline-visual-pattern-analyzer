import { Controller, Get, Res, Req } from '@nestjs/common';
import type { Response, Request } from 'express';
import * as fs from 'fs';
import * as path from 'path';

@Controller()
export class V4PageController {
  @Get('/')
  serveV4(@Req() req: Request, @Res() res: Response) {
    const accept = req.headers.accept || '';
    if (!accept.includes('text/html')) {
      res.status(404).send('Not Found');
      return;
    }
    const v4Path = path.join(process.cwd(), 'client', 'public', 'v4', 'index.html');
    if (fs.existsSync(v4Path)) {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      res.setHeader('Cache-Control', 'no-cache');
      fs.createReadStream(v4Path).pipe(res);
    } else {
      res.status(404).send('V4 page not found');
    }
  }
}
