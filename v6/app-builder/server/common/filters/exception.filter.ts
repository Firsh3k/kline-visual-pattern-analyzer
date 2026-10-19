import {
  ExceptionFilter,
  Catch,
  ArgumentsHost,
  HttpException,
  HttpStatus,
  Logger,
} from '@nestjs/common';
import type { Request, Response } from 'express';

/**
 * 全局异常过滤器：统一把异常转成 JSON，避免抛出 HTML/默认错误页（前端只按 JSON 解析）。
 * 同时保证 5xx 与 Python fail-closed 错误都有可追踪的 method/url 日志。
 */
@Catch()
export class GlobalExceptionFilter implements ExceptionFilter {
  private readonly logger = new Logger('GlobalException');

  catch(exception: unknown, host: ArgumentsHost): void {
    const ctx = host.switchToHttp();
    const res = ctx.getResponse<Response>();
    const req = ctx.getRequest<Request>();

    const status =
      exception instanceof HttpException
        ? exception.getStatus()
        : HttpStatus.INTERNAL_SERVER_ERROR;

    const rawResponse =
      exception instanceof HttpException
        ? exception.getResponse()
        : { message: (exception as Error)?.message || 'Internal Server Error' };

    const body =
      typeof rawResponse === 'string'
        ? { message: rawResponse }
        : (rawResponse as Record<string, unknown>);

    if (status >= HttpStatus.INTERNAL_SERVER_ERROR) {
      this.logger.error(
        `${req.method} ${req.url} -> ${status}: ${(exception as Error)?.message ?? exception}`,
        (exception as Error)?.stack,
      );
    } else {
      this.logger.warn(`${req.method} ${req.url} -> ${status}: ${(exception as Error)?.message ?? exception}`);
    }

    if (res.headersSent) {
      return;
    }
    res.status(status).json({
      statusCode: status,
      ...body,
      path: req.url,
      timestamp: new Date().toISOString(),
    });
  }
}
