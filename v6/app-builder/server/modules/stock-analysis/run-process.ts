/**
 * FIR-41 / QA R71-2：可中止、可确认退出的子进程执行器（无 Nest 依赖，tests/v6/test_scan_jobs.js 用真实进程验证）。
 *
 * - 只在子进程真正退出（'close'：进程结束且 stdio 关闭）后才 settle，调用方据此确认清理完成再释放并发名额。
 * - 超时或 signal 中止 → SIGKILL（不可被忽略），等退出后以 killed=true 返回，不留遗留进程。
 * - 不经过 shell，参数数组直传。
 */
import { spawn } from 'child_process';

export interface RunProcessOptions {
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  timeoutMs?: number;
  signal?: AbortSignal;
}

export interface RunProcessResult {
  stdout: string;
  stderr: string;
  exitCode: number | null;
  signal: NodeJS.Signals | null;
  killed: false | 'timeout' | 'aborted';
  pid: number | undefined;
}

export function runProcess(bin: string, args: string[], opts: RunProcessOptions = {}): Promise<RunProcessResult> {
  return new Promise((resolve, reject) => {
    if (opts.signal?.aborted) {
      resolve({ stdout: '', stderr: '', exitCode: null, signal: null, killed: 'aborted', pid: undefined });
      return;
    }
    const child = spawn(bin, args, { cwd: opts.cwd, env: opts.env, shell: false });
    let stdout = '';
    let stderr = '';
    let killed: RunProcessResult['killed'] = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const kill = (why: 'timeout' | 'aborted') => {
      if (killed) return;
      killed = why;
      try { child.kill('SIGKILL'); } catch { /* 已退出 */ }
    };
    const onAbort = () => kill('aborted');

    if (opts.timeoutMs && opts.timeoutMs > 0) timer = setTimeout(() => kill('timeout'), opts.timeoutMs);
    opts.signal?.addEventListener('abort', onAbort, { once: true });

    child.stdout?.on('data', (c: Buffer) => { stdout += c.toString(); });
    child.stderr?.on('data', (c: Buffer) => { stderr += c.toString(); });

    const cleanup = () => {
      if (timer) clearTimeout(timer);
      opts.signal?.removeEventListener('abort', onAbort);
    };
    child.on('error', (err) => {
      cleanup();
      reject(err);
    });
    child.on('close', (exitCode, signal) => {
      cleanup();
      resolve({ stdout, stderr, exitCode, signal, killed, pid: child.pid });
    });
  });
}
