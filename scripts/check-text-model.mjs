#!/usr/bin/env node

import { readFileSync } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function readDotEnv() {
  const values = {};
  try {
    const lines = readFileSync(path.join(repoRoot, '.env'), 'utf8').split(/\r?\n/);
    for (const line of lines) {
      const match = /^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/.exec(line.trim());
      if (match) values[match[1]] = match[2].trim().replace(/^['"]|['"]$/g, '');
    }
  } catch {
    // The CLI arguments below can still be used without a .env file.
  }
  return values;
}

function parseArgs(argv) {
  const args = {
    'base-url': null,
    model: null,
    'api-key': null,
    timeout: null,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!(key in args)) throw new Error(`Unknown option: ${key}`);
    const value = argv[index + 1];
    if (!value) throw new Error(`Missing value for ${key}`);
    args[key] = value;
    index += 1;
  }
  return args;
}

function parseModelContent(message) {
  const content = message?.content ?? message?.reasoning_content ?? '';
  if (Array.isArray(content)) {
    return content.map((part) => part?.text ?? '').join('');
  }
  return String(content ?? '');
}

function parseStrictJson(content) {
  let raw = content.trim();
  const thinking = /<think>.*?<\/think>/is.exec(raw);
  if (thinking) raw = raw.replace(thinking[0], '').trim();

  const fenced = /^```(?:json)?\s*([\s\S]*?)\s*```$/i.exec(raw);
  if (fenced) raw = fenced[1];
  const start = raw.indexOf('{');
  const end = raw.lastIndexOf('}');
  if (start < 0 || end <= start) throw new Error('response contains no JSON object');

  const parsed = JSON.parse(raw.slice(start, end + 1));
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('response JSON is not an object');
  }
  for (const [type, values] of Object.entries(parsed)) {
    if (!Array.isArray(values)) throw new Error(`type "${type}" does not map to an array`);
    if (!values.length) throw new Error(`type "${type}" maps to an empty array`);
    for (const value of values) {
      if (typeof value !== 'string' || !value.trim()) {
        throw new Error(`type "${type}" contains a non-string or empty entity`);
      }
    }
  }
  return parsed;
}

const dotenv = readDotEnv();
const args = parseArgs(process.argv.slice(2));
const baseUrl = (args['base-url'] || dotenv.HAS_TEXT_EXTERNAL_BASE_URL || 'http://192.168.2.238:1234/v1').replace(/\/+$/, '');
const model = args.model || dotenv.HAS_TEXT_MODEL_NAME || 'qwen3.8-27b-ud';
const apiKey = args['api-key'] || dotenv.HAS_TEXT_API_KEY || '';
const timeoutMs = Number(args.timeout || 120000);
const headers = apiKey ? { Authorization: `Bearer ${apiKey}` } : {};

if (!apiKey) {
  console.error('[FAIL] API key is empty. Set HAS_TEXT_API_KEY in .env or pass --api-key.');
  process.exit(1);
}

console.log(`[check] GET ${baseUrl}/models`);
const modelsResponse = await fetch(`${baseUrl}/models`, {
  headers,
  signal: AbortSignal.timeout(Math.min(timeoutMs, 15000)),
});
if (!modelsResponse.ok) {
  console.error(`[FAIL] /models returned HTTP ${modelsResponse.status} ${modelsResponse.statusText}`);
  console.error(await modelsResponse.text().catch(() => ''));
  process.exit(1);
}
const modelsPayload = await modelsResponse.json();
const availableModels = (modelsPayload.data ?? []).map((item) => item.id);
if (!availableModels.includes(model)) {
  console.error(`[FAIL] model "${model}" is unavailable. Available models: ${availableModels.join(', ') || '(none)'}`);
  process.exit(1);
}
console.error(`[OK] model available: ${model}`);

const prompt = `Recognize the following entity types in the text.
Specified types:["姓名","电话"]

Return strict JSON only. Include only entity types that have matches in the text.
Never output empty arrays. Do not return requested types with no matches. Do not explain.
If nothing matches, return {}.
<text>张三 电话 13812345678</text>`;

console.error(`[check] POST ${baseUrl}/chat/completions`);
const response = await fetch(`${baseUrl}/chat/completions`, {
  method: 'POST',
  headers: { ...headers, 'Content-Type': 'application/json' },
  body: JSON.stringify({
    model,
    messages: [{ role: 'user', content: prompt }],
    temperature: 0,
    top_p: 0.6,
    max_tokens: 1024,
    stream: false,
  }),
  signal: AbortSignal.timeout(timeoutMs),
});
const responseText = await response.text();
if (!response.ok) {
  console.error(`[FAIL] /chat/completions returned HTTP ${response.status} ${response.statusText}`);
  console.error(responseText);
  process.exit(1);
}

let payload;
try {
  payload = JSON.parse(responseText);
} catch {
  console.error('[FAIL] response is not valid JSON');
  console.error(responseText);
  process.exit(1);
}

const message = payload.choices?.[0]?.message;
if (!message) {
  console.error('[FAIL] response has no choices[0].message');
  console.error(JSON.stringify(payload, null, 2));
  process.exit(1);
}

const content = parseModelContent(message);
let entities;
try {
  entities = parseStrictJson(content);
} catch (error) {
  console.error(`[FAIL] strict JSON validation failed: ${error.message}`);
  console.error(content || '(empty content)');
  process.exit(1);
}

console.error('[OK] strict JSON validation passed');
console.error(JSON.stringify(entities, null, 2));
