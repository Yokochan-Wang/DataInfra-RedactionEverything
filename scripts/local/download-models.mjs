#!/usr/bin/env node
// 下载本机单栈需要的模型文件（当前：HaS Text GGUF）。
// 用法：npm run model:has    已存在且大小一致则自动跳过
import { createWriteStream, existsSync, mkdirSync, readFileSync, statSync } from 'node:fs';
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
const configured = env.HAS_MODEL_PATH || 'backend/models/has/HaS_Text_0209_0.6B_Q4_K_M.gguf';
const dest = path.isAbsolute(configured) ? configured : path.join(repoRoot, configured);
const name = path.basename(dest);
const sources = [
  `https://hf-mirror.com/xuanwulab/HaS_Text_0209_0.6B_Q4/resolve/main/${name}`,
  `https://huggingface.co/xuanwulab/HaS_Text_0209_0.6B_Q4/resolve/main/${name}`,
];

async function expectedSize(url) {
  const response = await fetch(url, { method: 'HEAD', redirect: 'follow', signal: AbortSignal.timeout(30000) });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return Number(response.headers.get('content-length') || 0);
}

async function download(url, expected) {
  const response = await fetch(url, { redirect: 'follow' });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const out = createWriteStream(dest);
  let done = 0;
  let lastPct = -10;
  for await (const chunk of response.body) {
    done += chunk.length;
    const pct = expected ? Math.floor((done / expected) * 100) : 0;
    if (pct >= lastPct + 5) {
      lastPct = pct;
      console.log(`[model] ${(done / 1048576).toFixed(0)} / ${(expected / 1048576).toFixed(0)} MB (${pct}%)`);
    }
    if (!out.write(chunk)) await new Promise((resolve) => out.once('drain', resolve));
  }
  await new Promise((resolve, reject) => {
    out.end(() => (out.destroyed ? resolve() : resolve()));
    out.on('error', reject);
  });
  return done;
}

async function main() {
  mkdirSync(path.dirname(dest), { recursive: true });
  let lastError = '';
  for (const url of sources) {
    let expected = 0;
    try {
      expected = await expectedSize(url);
    } catch (error) {
      lastError = `${url} -> ${error.message}`;
      console.log(`[model] 探测失败：${lastError}，换下一个源`);
      continue;
    }
    const local = existsSync(dest) ? statSync(dest).size : 0;
    if (expected > 0 && local === expected) {
      console.log(`[model] 已就绪，跳过下载：${dest}`);
      return;
    }
    console.log(`[model] 开始下载 ${url}`);
    console.log(`[model] 目标 ${dest}（${(expected / 1048576).toFixed(0)} MB，本地已有 ${(local / 1048576).toFixed(0)} MB）`);
    try {
      const written = await download(url, expected);
      if (expected > 0 && written !== expected) {
        lastError = `写入 ${written} 字节，与期望 ${expected} 不符`;
        console.log(`[model] 不完整：${lastError}，换源重试`);
        continue;
      }
      console.log('[model] 下载完成');
      return;
    } catch (error) {
      lastError = `${url} -> ${error?.message || error}`;
      console.log(`[model] 下载失败：${lastError}，换下一个源`);
    }
  }
  console.error(`[model] 全部源均失败，最后错误：${lastError}`);
  process.exit(1);
}

main().catch((error) => {
  console.error(`[model] 异常：${error?.stack || error}`);
  process.exit(1);
});
