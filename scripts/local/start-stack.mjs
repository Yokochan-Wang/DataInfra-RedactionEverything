#!/usr/bin/env node
// Windows 单栈本地启动器（不依赖 WSL / Docker）。
//
//   llama-server (8080)  ->  HaS Text GGUF 文本 NER
//   ocr_server.py (8082) ->  PP-StructureV3 OCR（Paddle GPU）
//   uvicorn     (8000)   ->  FastAPI 后端
//   vite        (3000)   ->  前端
//
// 配置来源：仓库根目录 .env（后端 settings 只读 backend/.env，所以必须由本脚本注入环境变量）。
// 用法：node scripts/local/start-stack.mjs     Ctrl+C 或关闭窗口即停止
import { spawn, spawnSync } from 'node:child_process';
import { createWriteStream, existsSync, mkdirSync, readFileSync, readdirSync } from 'node:fs';
import os from 'node:os';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, '..', '..');
const backendDir = path.join(repoRoot, 'backend');
const frontendDir = path.join(repoRoot, 'frontend');
const logsDir = path.join(repoRoot, 'logs');
mkdirSync(logsDir, { recursive: true });

class StartupError extends Error {
  constructor(detail) {
    super(detail);
    this.detail = detail;
  }
}

function parseEnv(filePath) {
  if (!existsSync(filePath)) return {};
  const values = {};
  for (const raw of readFileSync(filePath, 'utf8').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const match = line.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (!match) continue;
    let value = match[2].trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    values[match[1]] = value;
  }
  return values;
}

const fileEnv = parseEnv(path.join(repoRoot, '.env'));
const env = { ...process.env, ...fileEnv, PYTHONUNBUFFERED: '1' };

const venvDir = env.WINDOWS_VENV_DIR || '.venv';
const python = env.WINDOWS_PYTHON || path.join(repoRoot, venvDir, 'Scripts', 'python.exe');

function portOf(url, fallback) {
  const match = String(url || '').match(/^https?:\/\/[^/]*?:(\d+)/i);
  return match ? Number(match[1]) : fallback;
}

const nerBaseUrl = (env.HAS_LLAMACPP_BASE_URL || 'http://127.0.0.1:8080/v1').replace(/\/+$/, '');
const nerPort = Number(env.HAS_TEXT_PORT || portOf(nerBaseUrl, 8080));
const nerModel = (env.HAS_TEXT_MODEL_NAME || 'HaS_Text_0209_0.6B_Q4').trim();
const ocrBaseUrl = (env.OCR_BASE_URL || 'http://127.0.0.1:8082').replace(/\/+$/, '');
const ocrPort = Number(env.OCR_PORT || portOf(ocrBaseUrl, 8082));
const backendPort = Number(env.BACKEND_PORT || 8000);
const frontendPort = Number(env.FRONTEND_PORT || 3000);
const ocrEnabled = !['0', 'false', 'no'].includes(String(env.LOCAL_OCR_ENABLED ?? '1').trim().toLowerCase());
const backendHost = env.BIND_HOST || '0.0.0.0';

const children = [];

function resolveLocal(value) {
  if (!value) return '';
  return path.isAbsolute(value) ? value : path.join(repoRoot, value);
}

// Paddle/torch Windows wheels ship CUDA DLLs under site-packages/nvidia/*/bin.
// They are not on PATH by default, so prepend them for the GPU services.
function nvidiaDllDirs() {
  const root = path.join(path.dirname(python), '..', 'Lib', 'site-packages', 'nvidia');
  if (!existsSync(root)) return [];
  const dirs = [];
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const bin = path.join(root, entry.name, 'bin');
    if (existsSync(bin)) dirs.push(bin);
  }
  return dirs;
}

function buildPath() {
  return [...nvidiaDllDirs(), path.dirname(python), path.dirname(process.execPath), process.env.PATH || ''].filter(Boolean).join(path.delimiter);
}

function logPath(name) {
  return path.join(logsDir, `${name}.log`);
}

function spawnLogged(name, command, args, options = {}) {
  const out = createWriteStream(logPath(name), { flags: 'a' });
  out.write(`\n\n===== ${new Date().toISOString()} ${command} ${args.join(' ')} =====\n`);
  const child = spawn(command, args, {
    cwd: options.cwd || repoRoot,
    env: options.env || { ...env, PYTHONPATH: backendDir },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  children.push(child);
  child.exited = new Promise((resolve) => {
    child.on('exit', (code, signal) => resolve({ code, signal }));
    child.on('error', () => resolve({ code: null, signal: null }));
  });
  for (const [stream, sink] of [[child.stdout, out], [child.stderr, out]]) {
    let buffer = '';
    stream.on('data', (chunk) => {
      buffer += chunk.toString();
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() || '';
      for (const line of lines) {
        const text = `[${name}] ${line}\n`;
        process.stdout.write(text);
        sink.write(text);
      }
    });
  }
  child.on('exit', (code, signal) => {
    const message = `[${name}] 进程退出 code=${code ?? ''} signal=${signal ?? ''}\n`;
    process.stdout.write(message);
    out.write(message);
  });
  console.log(`[stack] 启动 ${name} pid=${child.pid}`);
  return child;
}

function probePort(port, host = '127.0.0.1') {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host, port, timeout: 1000 });
    socket.on('connect', () => { socket.destroy(); resolve(true); });
    socket.on('timeout', () => { socket.destroy(); resolve(false); });
    socket.on('error', () => resolve(false));
  });
}

async function ensurePortFree(port, label) {
  if (await probePort(port)) {
    throw new StartupError(`端口 ${port}（${label}）已被占用。请先双击 stop-app.bat（或执行 npm run stop:local）清理旧进程，再重新启动`);
  }
}

async function waitPort(port, label, timeoutMs = 600000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await probePort(port)) return;
    await new Promise((r) => setTimeout(r, 1500));
  }
  throw new StartupError(`${label} 在 ${timeoutMs / 1000}s 内没有监听端口 ${port}`);
}

async function waitJson(url, predicate, label, timeoutMs, child = null) {
  let last = '';
  const poll = async () => {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      try {
        const response = await fetch(url, { signal: AbortSignal.timeout(8000) });
        if (response.ok) {
          const body = await response.json();
          if (!predicate || predicate(body)) return body;
          last = JSON.stringify(body).slice(0, 240);
        } else {
          last = `${response.status} ${response.statusText}`;
        }
      } catch (error) {
        last = error?.name === 'TimeoutError' ? 'timeout' : String(error?.cause?.message || error?.message || '请求失败');
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
    throw new StartupError(`${label} 未在 ${timeoutMs / 1000}s 内就绪，最后一次探测：${last || '无响应'}`);
  };
  if (!child) return poll();
  const crashed = child.exited.then(({ code, signal }) => {
    throw new StartupError(`${label} 进程提前退出（code=${code ?? ''} signal=${signal ?? ''}），查看 ${logPath(label)}.log`);
  });
  crashed.catch(() => {});
  return Promise.race([poll(), crashed]);
}

function killAll() {
  for (const child of children.reverse()) {
    if (!child.pid || child.killed) continue;
    if (process.platform === 'win32') {
      spawnSync('taskkill', ['/T', '/F', '/PID', String(child.pid)], { stdio: 'ignore' });
    } else {
      child.kill('SIGTERM');
    }
  }
}

async function startNer() {
  const runtime = String(env.HAS_TEXT_RUNTIME || '').trim().toLowerCase();
  if (runtime !== 'llamacpp') {
    throw new StartupError(`start-stack.mjs 只支持 HAS_TEXT_RUNTIME=llamacpp，当前为 "${runtime}"（请检查 .env）`);
  }
  const exe = resolveLocal(env.LLAMA_SERVER_EXE || '');
  if (!exe || !existsSync(exe)) {
    throw new StartupError(`找不到 llama-server：${env.LLAMA_SERVER_EXE || '(LLAMA_SERVER_EXE 未设置)'}`);
  }
  const modelPath = resolveLocal(env.HAS_MODEL_PATH || '');
  if (!modelPath || !existsSync(modelPath)) {
    throw new StartupError(`找不到 HaS Text GGUF：${env.HAS_MODEL_PATH || '(HAS_MODEL_PATH 未设置)'}，先执行 npm run model:has`);
  }
  await ensurePortFree(nerPort, 'has-ner');
  const args = [
    '-m', modelPath,
    '--alias', nerModel,
    '--host', '127.0.0.1',
    '--port', String(nerPort),
    '--ctx-size', String(env.LLAMA_SERVER_CTX || 8192),
    '--n-gpu-layers', String(env.LLAMA_SERVER_GPU_LAYERS ?? -1),
    '--threads', String(env.LLAMA_SERVER_THREADS || Math.max(2, os.cpus().length - 2)),
  ];
  const extra = String(env.LLAMA_SERVER_EXTRA_ARGS || '').trim();
  if (extra) args.push(...extra.split(/\s+/));
  const child = spawnLogged('has-ner', exe, args, { env });
  await waitJson(
    `${nerBaseUrl}/models`,
    (body) => Array.isArray(body.data) && body.data.length > 0,
    'has-ner',
    240000,
    child,
  );
  console.log(`[stack] 文本 NER 就绪：${nerBaseUrl} (${nerModel})`);
}

async function startOcr() {
  if (!ocrEnabled) {
    console.log('[stack] 跳过 OCR 服务（LOCAL_OCR_ENABLED=0），图片/PDF/Word 识别不可用');
    return;
  }
  const script = path.join(backendDir, 'scripts', 'ocr_server.py');
  if (!existsSync(script)) throw new StartupError(`找不到 ${script}`);
  await ensurePortFree(ocrPort, 'ocr');
  const ocrEnv = {
    ...env,
    PYTHONPATH: backendDir,
    OCR_PORT: String(ocrPort),
    OCR_STRUCTURE_ENABLED: env.OCR_STRUCTURE_ENABLED || '1',
    OCR_STRUCTURE_PRIMARY: '1',
    OCR_VL_ENABLED: env.OCR_VL_ENABLED || '0',
    PADDLE_PDX_MODEL_SOURCE: env.PADDLE_PDX_MODEL_SOURCE || 'modelscope',
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK: env.PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK || 'True',
    PATH: buildPath(),
  };
  const child = spawnLogged('ocr', python, ['scripts/ocr_server.py'], { cwd: backendDir, env: ocrEnv });
  await waitPort(ocrPort, 'ocr', 900000);
  await waitJson(`http://127.0.0.1:${ocrPort}/health`, (body) => body.ready === true, 'ocr', 900000, child);
  console.log(`[stack] OCR 就绪：http://127.0.0.1:${ocrPort}`);
}

async function startBackend() {
  await ensurePortFree(backendPort, 'backend');
  const backendEnv = {
    ...env,
    PYTHONPATH: backendDir,
    PATH: buildPath(),
    // 后端 settings 只读 backend/.env，这里显式注入根 .env 的关键项
    HAS_TEXT_RUNTIME: env.HAS_TEXT_RUNTIME,
    HAS_LLAMACPP_BASE_URL: nerBaseUrl,
    HAS_TEXT_MODEL_NAME: nerModel,
    HAS_MODEL_PATH: resolveLocal(env.HAS_MODEL_PATH || ''),
    OCR_BASE_URL: `http://127.0.0.1:${ocrPort}`,
    LOCATE_ANYTHING_ENABLED: env.LOCATE_ANYTHING_ENABLED || '0',
    OCR_VL_ENABLED: env.OCR_VL_ENABLED || '0',
  };
  const child = spawnLogged(
    'backend',
    python,
    ['-m', 'uvicorn', 'app.main:app', '--host', backendHost, '--port', String(backendPort)],
    { cwd: backendDir, env: backendEnv },
  );
  const body = await waitJson(
    `http://127.0.0.1:${backendPort}/health/services`,
    (payload) => payload.services?.has_ner?.status === 'online'
      && (ocrEnabled ? payload.services?.paddle_ocr?.status === 'online' : true),
    'backend',
    300000,
    child,
  );
  for (const [key, value] of Object.entries(body.services || {})) {
    console.log(`[stack]   ${key}: ${value.status}${value.name ? ` (${value.name})` : ''}`);
  }
}

async function startFrontend() {
  await ensurePortFree(frontendPort, 'frontend');
  if (!existsSync(path.join(frontendDir, 'node_modules'))) {
    throw new StartupError('frontend/node_modules 缺失，先在 frontend 目录执行 npm install');
  }
  const npmCommand = process.platform === 'win32' ? 'npm.cmd' : 'npm';
  spawnLogged(
    'frontend',
    'cmd.exe',
    ['/d', '/s', '/c', `${npmCommand} run dev -- --host 0.0.0.0 --port ${frontendPort} --strictPort`],
    { cwd: frontendDir, env: { ...process.env, PATH: buildPath() } },
  );
  await waitPort(frontendPort, 'frontend', 180000);
}

function preflight() {
  if (process.platform !== 'win32') {
    throw new StartupError('本脚本面向 Windows 单栈；WSL 混合模式请用 npm run dev');
  }
  if (!existsSync(python)) {
    throw new StartupError(`找不到 Python 虚拟环境：${python}`);
  }
}

async function main() {
  preflight();
  console.log('[stack] Windows 单栈启动中…… 日志目录 logs/');
  await startNer();
  await startOcr();
  await startBackend();
  await startFrontend();
  console.log('');
  console.log(`[stack] 全部就绪  ->  打开 http://localhost:${frontendPort}`);
  console.log(`[stack] 后端 API  ->  http://localhost:${backendPort}/docs`);
  console.log('[stack] 停止方式：本窗口按 Ctrl+C / 直接关闭本窗口 / 双击 stop-app.bat');
  await new Promise(() => {});
}

process.on('SIGINT', () => { console.log('[stack] 正在停止……'); killAll(); process.exit(0); });
process.on('SIGTERM', () => { killAll(); process.exit(0); });

main().catch((err) => {
  console.error(`[stack] 启动失败：${err instanceof StartupError ? err.detail : err?.stack || err}`);
  killAll();
  process.exit(1);
});
