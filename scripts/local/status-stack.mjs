#!/usr/bin/env node
// Windows 单栈状态查看：四个服务的端口占用 + 后端聚合健康。
// 用法：node scripts/local/status-stack.mjs
import { existsSync, readFileSync } from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

function readEnv() {
  const values = {};
  const file = path.join(repoRoot, '.env');
  if (!existsSync(file)) return values;
  for (const raw of readFileSync(file, 'utf8').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const match = line.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (match) values[match[1]] = match[2].trim();
  }
  return values;
}

const env = { ...process.env, ...readEnv() };
const portOf = (url, fallback) => {
  const match = String(url || '').match(/^https?:\/\/[^/]*?:(\d+)/i);
  return match ? Number(match[1]) : fallback;
};

const nerPort = Number(env.HAS_TEXT_PORT || portOf(env.HAS_LLAMACPP_BASE_URL, 8080));
const ocrPort = Number(env.OCR_PORT || portOf(env.OCR_BASE_URL, 8082));
const backendPort = Number(env.BACKEND_PORT || 8000);
const frontendPort = Number(env.FRONTEND_PORT || 3000);
const targets = [
  { label: '文本 NER  llama-server', port: nerPort },
  { label: 'OCR      PP-StructureV3', port: ocrPort },
  { label: '后端     FastAPI', port: backendPort },
  { label: '前端     vite', port: frontendPort },
];

function probePort(port) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host: '127.0.0.1', port, timeout: 1200 });
    socket.on('connect', () => { socket.destroy(); resolve(true); });
    socket.on('timeout', () => { socket.destroy(); resolve(false); });
    socket.on('error', () => resolve(false));
  });
}

async function probeJson(url) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(6000) });
    if (!response.ok) return { error: String(response.status) };
    return await response.json();
  } catch (error) {
    return { error: String(error?.cause?.code || error?.message || 'unreachable') };
  }
}

for (const target of targets) {
  target.listening = await probePort(target.port);
}

const health = targets[2].listening ? await probeJson(`http://127.0.0.1:${backendPort}/health/services`) : {};
const services = health.services || {};

// /health/services 的 detail 是一个字段包，只挑人看得懂的几项打印。
function detailText(detail) {
  if (!detail || typeof detail !== 'object') return '';
  const keys = ['runtime_mode', 'device', 'model_state', 'error'];
  return keys.map((key) => detail[key]).filter((value) => typeof value === 'string' && value).join(' ');
}

console.log('');
for (const target of targets) {
  console.log(`${target.listening ? '[在线]' : '[离线]'} ${target.label.padEnd(24)} :${target.port}`);
}
console.log('');
for (const key of ['has_ner', 'paddle_ocr', 'visual_features']) {
  const value = services[key];
  if (!value) continue;
  const extra = [value.name, detailText(value.detail)].filter(Boolean).join(' ');
  console.log(`后端探测 ${key} = ${value.status}${extra ? ` (${extra})` : ''}`);
}
if (health.gpu_memory) {
  console.log(`显存占用 ${health.gpu_memory.used_mb} MB / ${health.gpu_memory.total_mb} MB`);
}
if (services.visual_features?.status === 'offline') {
  console.log('提示：视觉特征链（LocateAnything-3B）按 .env 关闭，文字/扫描件 OCR 脱敏不受影响。');
}
console.log('');
if (targets.every((target) => !target.listening)) {
  console.log('没有本地服务在运行。启动：双击 start-app.bat 或执行 npm run dev:local');
} else if (targets.every((target) => target.listening)) {
  console.log(`可以打开 http://localhost:${frontendPort}`);
}
