#!/usr/bin/env node
// Windows 单栈停止器：按端口 + 进程特征清理本项目启动的 4 个服务。
// 用法：node scripts/local/stop-stack.mjs
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, '..', '..');

const env = (() => {
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
})();

const portOf = (url, fallback) => {
  const match = String(url || '').match(/^https?:\/\/[^/]*?:(\d+)/i);
  return match ? Number(match[1]) : fallback;
};

const ports = [
  Number(env.FRONTEND_PORT || 3000),
  Number(env.BACKEND_PORT || 8000),
  Number(env.HAS_TEXT_PORT || portOf(env.HAS_LLAMACPP_BASE_URL, 8080)),
  Number(env.OCR_PORT || portOf(env.OCR_BASE_URL, 8082)),
];

if (process.platform !== 'win32') {
  console.log('[stop] 目前只支持 Windows 单栈停止；WSL 模式请用 npm run stop');
  process.exit(0);
}

// Signatures match on the command line, so an unrelated llama-server or python on the
// same machine is never touched: the HaS model name and this repo's own script path are
// the discriminators.
const script = [
  "$ErrorActionPreference='SilentlyContinue'",
  `$ports=@(${ports.join(',')})`,
  '$victims=@()',
  'foreach($port in $ports){',
  '  Get-NetTCPConnection -LocalPort $port -State Listen | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { if($_){ $victims += $_ } }',
  '}',
  "$sig=@(",
  "  @('llama-server.exe','*HaS_Text*'),",
  "  @('python.exe','*uvicorn app.main:app*'),",
  "  @('python.exe','*ocr_server.py*'),",
  "  @('node.exe','*start-stack.mjs*')",
  ')',
  'foreach($t in $sig){',
  "  Get-CimInstance Win32_Process -Filter \"Name='$($t[0])'\" | Where-Object { $_.CommandLine -like $t[1] } | ForEach-Object { $victims += $_.ProcessId }",
  '}',
  '$victims = $victims | Select-Object -Unique',
  'foreach($pid_ in $victims){',
  '  if($pid_ -eq $PID){ continue }',
  "  & taskkill /T /F /PID $pid_ 2>&1 | Out-Null",
  "  Write-Output ('[stop] 已结束进程树 PID=' + $pid_)",
  '}',
  'Start-Sleep -Milliseconds 500',
  'foreach($port in $ports){',
  '  $left = Get-NetTCPConnection -LocalPort $port -State Listen',
  "  if($left){ Write-Output ('[stop] WARN port ' + $port + ' still in use') }",
  '}',
].join('\n');

const result = spawnSync('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', script], {
  stdio: 'inherit',
  env: { ...process.env, PYTHONUTF8: '1' },
});

// Stopping is best-effort: a port that was already free makes PowerShell report a
// suppressed error, which must not surface as a failed stop.
console.log('[stop] done; ports checked: ' + ports.join(', '));
process.exit(0);
