import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { existsSync, statSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT_DIR = __dirname;
const HOST = process.env.OPS_HOST || '127.0.0.1';
const PORT = Number(process.env.OPS_PORT || 8787);

const pythonCandidates = [
  process.env.OPS_PYTHON,
  '/Users/ljjjjj/miniconda3/bin/python',
  'python3',
].filter(Boolean);

const jobs = {
  postclose: {
    key: 'postclose',
    label: '短线多因子-盘后版',
    scriptPath: path.join(ROOT_DIR, 'scripts', 'postclose_screen.py'),
    top5CsvPath: path.join(ROOT_DIR, 'docs', 'list', 'postclose_top5.csv'),
    top5MdPath: path.join(ROOT_DIR, 'docs', 'list', 'postclose_top5.md'),
  },
  tail: {
    key: 'tail',
    label: '短线多因子-尾盘版',
    scriptPath: path.join(ROOT_DIR, 'scripts', 'tail_screen.py'),
    top5CsvPath: path.join(ROOT_DIR, 'docs', 'list', 'tail_top5.csv'),
    top5MdPath: path.join(ROOT_DIR, 'docs', 'list', 'tail_top5.md'),
  },
};

const jobState = Object.fromEntries(
  Object.keys(jobs).map((key) => [
    key,
    {
      status: 'idle',
      running: false,
      startedAt: null,
      finishedAt: null,
      exitCode: null,
      output: [],
      pid: null,
      pythonBin: pythonCandidates[0] || 'python3',
    },
  ])
);

function appendOutput(state, chunk) {
  const text = String(chunk || '').replace(/\r/g, '');
  const lines = text.split('\n').filter(Boolean);
  if (!lines.length) {
    return;
  }
  state.output.push(...lines);
  if (state.output.length > 200) {
    state.output = state.output.slice(-200);
  }
}

function json(res, statusCode, payload) {
  res.writeHead(statusCode, {
    'Content-Type': 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  });
  res.end(JSON.stringify(payload));
}

function snapshotState(key) {
  const state = jobState[key];
  return {
    key,
    label: jobs[key].label,
    ...state,
  };
}

async function readTop5(key) {
  const job = jobs[key];
  const csvExists = existsSync(job.top5CsvPath);
  const mdExists = existsSync(job.top5MdPath);
  const exists = csvExists || mdExists;

  if (!exists) {
    return {
      key,
      label: job.label,
      exists: false,
      csvText: '',
      markdown: '',
      updatedAt: null,
    };
  }

  const [csvText, markdown] = await Promise.all([
    csvExists ? readFile(job.top5CsvPath, 'utf8') : Promise.resolve(''),
    mdExists ? readFile(job.top5MdPath, 'utf8') : Promise.resolve(''),
  ]);

  const latestPath = mdExists ? job.top5MdPath : job.top5CsvPath;
  const updatedAt = statSync(latestPath).mtime.toISOString();

  return {
    key,
    label: job.label,
    exists: true,
    csvText,
    markdown,
    updatedAt,
  };
}

function startJob(key) {
  const job = jobs[key];
  const state = jobState[key];
  if (state.running) {
    return { ok: false, reason: 'already-running', state: snapshotState(key) };
  }

  const pythonBin = pythonCandidates[0] || 'python3';
  state.status = 'running';
  state.running = true;
  state.startedAt = new Date().toISOString();
  state.finishedAt = null;
  state.exitCode = null;
  state.output = [`[start] ${job.label}`, `[python] ${pythonBin}`, `[script] ${path.relative(ROOT_DIR, job.scriptPath)}`];
  state.pythonBin = pythonBin;

  const child = spawn(pythonBin, [job.scriptPath], {
    cwd: ROOT_DIR,
    env: {
      ...process.env,
      PYTHONUTF8: '1',
    },
  });

  state.pid = child.pid || null;

  child.stdout.on('data', (chunk) => appendOutput(state, chunk));
  child.stderr.on('data', (chunk) => appendOutput(state, chunk));

  child.on('error', (error) => {
    appendOutput(state, `[spawn-error] ${error.message}`);
    state.status = 'error';
    state.running = false;
    state.finishedAt = new Date().toISOString();
    state.exitCode = -1;
    state.pid = null;
  });

  child.on('close', (code) => {
    state.status = code === 0 ? 'success' : 'error';
    state.running = false;
    state.finishedAt = new Date().toISOString();
    state.exitCode = code;
    state.pid = null;
    appendOutput(state, `[done] exit=${code}`);
  });

  return { ok: true, state: snapshotState(key) };
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url || '/', `http://${req.headers.host || `${HOST}:${PORT}`}`);

  if (req.method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    });
    res.end();
    return;
  }

  if (req.method === 'GET' && url.pathname === '/api/health') {
    json(res, 200, {
      ok: true,
      host: HOST,
      port: PORT,
      jobs: Object.keys(jobs),
      pythonCandidates,
    });
    return;
  }

  if (req.method === 'GET' && url.pathname === '/api/jobs') {
    json(res, 200, { jobs: Object.keys(jobs).map((key) => snapshotState(key)) });
    return;
  }

  const jobStatusMatch = url.pathname.match(/^\/api\/jobs\/(postclose|tail)$/);
  if (req.method === 'GET' && jobStatusMatch) {
    const key = jobStatusMatch[1];
    json(res, 200, snapshotState(key));
    return;
  }

  const jobRunMatch = url.pathname.match(/^\/api\/run\/(postclose|tail)$/);
  if (req.method === 'POST' && jobRunMatch) {
    const key = jobRunMatch[1];
    const result = startJob(key);
    json(res, result.ok ? 202 : 409, result);
    return;
  }

  const top5Match = url.pathname.match(/^\/api\/top5\/(postclose|tail)$/);
  if (req.method === 'GET' && top5Match) {
    try {
      const payload = await readTop5(top5Match[1]);
      json(res, 200, payload);
    } catch (error) {
      json(res, 500, { error: error.message || 'read-top5-failed' });
    }
    return;
  }

  json(res, 404, { error: 'not-found' });
});

server.listen(PORT, HOST, () => {
  console.log(`[ops-api] http://${HOST}:${PORT}`);
});