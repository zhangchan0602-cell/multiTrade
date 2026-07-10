import { createServer } from 'node:http';
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, readdirSync, statSync } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseCsv } from './src/lib/csv.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT_DIR = __dirname;
const HOST = process.env.OPS_HOST || '127.0.0.1';
const PORT = Number(process.env.OPS_PORT || 8787);
const COMBINED_RULE_TEXT = '当天同时进入短线盘后版 Top10、RPS双90 Top20、龙头抱团 Top20 中的任意两个榜单';
const COMBINED_CURRENT_CSV_PATH = path.join(ROOT_DIR, 'docs', 'list', 'combined_top20.csv');
const COMBINED_CURRENT_MD_PATH = path.join(ROOT_DIR, 'docs', 'list', 'combined_top20.md');
const KECHUANG_INDEX_CODE = '000688.SH';
const KECHUANG_INDEX_CSV_PATH = path.join(ROOT_DIR, 'scripts', '.cache', 'index', '000688.csv');
const KECHUANG_DOWNLOAD_SCRIPT_PATH = path.join(ROOT_DIR, 'scripts', 'download_kechuang_index.py');
const INDUSTRY_TREND_RANK_PATH = path.join(ROOT_DIR, 'docs', 'list', 'industry_trend_rank.json');
const INDUSTRY_TREND_SCRIPT_PATH = path.join(ROOT_DIR, 'scripts', 'industry_trend_rank.py');

function resolvePythonCandidates(candidates) {
  const seen = new Set();
  return candidates.filter((candidate) => {
    const value = String(candidate || '').trim();
    if (!value || seen.has(value)) {
      return false;
    }
    seen.add(value);

    if (path.isAbsolute(value)) {
      return existsSync(value);
    }

    const probe = spawnSync(value, ['--version'], {
      cwd: ROOT_DIR,
      env: {
        ...process.env,
        PYTHONUTF8: '1',
      },
      stdio: 'ignore',
    });
    return !probe.error && probe.status === 0;
  });
}

const pythonCandidates = resolvePythonCandidates([
  process.env.OPS_PYTHON,
  '/usr/local/bin/python3',
  'python3',
  'python',
]);
const DEFAULT_PYTHON_BIN = pythonCandidates[0] || process.env.OPS_PYTHON || 'python3';
const SETTLEMENT_SUMMARY_PREFIX = '[tail-settle-summary]';

const jobs = {
  postclose: {
    key: 'postclose',
    label: '策略-多因子盘后',
    scriptPath: path.join(ROOT_DIR, 'scripts', 'short_screen.py'),
    top5CsvPath: path.join(ROOT_DIR, 'docs', 'list', 'short_top5.csv'),
    top5MdPath: path.join(ROOT_DIR, 'docs', 'list', 'short_top5.md'),
  },
  tail: {
    key: 'tail',
    label: '策略-收盘资金多因子',
    scriptPath: path.join(ROOT_DIR, 'scripts', 'tail_screen.py'),
    top5CsvPath: path.join(ROOT_DIR, 'docs', 'list', 'tail_top5.csv'),
    top5MdPath: path.join(ROOT_DIR, 'docs', 'list', 'tail_top5.md'),
  },
  rps90: {
    key: 'rps90',
    label: '策略-RPS双90',
    scriptPath: path.join(ROOT_DIR, 'scripts', 'rps90_screen.py'),
    top5CsvPath: path.join(ROOT_DIR, 'docs', 'list', 'rps90_top5.csv'),
    top5MdPath: path.join(ROOT_DIR, 'docs', 'list', 'rps90_top5.md'),
  },
  leader: {
    key: 'leader',
    label: '策略-龙头抱团',
    scriptPath: path.join(ROOT_DIR, 'scripts', 'leader_screen.py'),
    top5CsvPath: path.join(ROOT_DIR, 'docs', 'list', 'leader_top5.csv'),
    top5MdPath: path.join(ROOT_DIR, 'docs', 'list', 'leader_top5.md'),
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
      pythonBin: DEFAULT_PYTHON_BIN,
      settlementSummary: null,
    },
  ])
);

function resolveLatestShortOutputDir() {
  const historyRoot = path.join(ROOT_DIR, 'docs', 'list', 'history', 'short');
  if (!existsSync(historyRoot)) {
    return null;
  }

  const latestDirName = readdirSync(historyRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && /^\d{4}-\d{2}-\d{2}$/.test(entry.name))
    .map((entry) => entry.name)
    .sort()
    .at(-1);

  return latestDirName ? path.join(historyRoot, latestDirName) : null;
}

function resolveTop5Paths(job) {
  if (job.key !== 'postclose') {
    return {
      csvPath: job.top5CsvPath,
      mdPath: job.top5MdPath,
    };
  }

  const latestShortDir = resolveLatestShortOutputDir();
  if (!latestShortDir) {
    return {
      csvPath: job.top5CsvPath,
      mdPath: job.top5MdPath,
    };
  }

  const datedCsvPath = path.join(latestShortDir, 'short_top5.csv');
  const datedMdPath = path.join(latestShortDir, 'short_top5.md');
  if (existsSync(datedCsvPath) || existsSync(datedMdPath)) {
    return {
      csvPath: datedCsvPath,
      mdPath: datedMdPath,
    };
  }

  return {
    csvPath: job.top5CsvPath,
    mdPath: job.top5MdPath,
  };
}

function resolveShortListPath(fileName) {
  const latestShortDir = resolveLatestShortOutputDir();
  if (latestShortDir) {
    const datedPath = path.join(latestShortDir, fileName);
    if (existsSync(datedPath)) {
      return datedPath;
    }
  }

  return path.join(ROOT_DIR, 'docs', 'list', fileName);
}

function normalizeCode(code) {
  return String(code || '').trim().padStart(6, '0');
}

function toNumber(value) {
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

function normalizeTradeDate(value) {
  const digits = String(value || '').replace(/\D/g, '');
  return digits.length === 8 ? digits : null;
}

function formatTradeDateLabel(tradeDate) {
  const normalized = normalizeTradeDate(tradeDate);
  if (!normalized) {
    return '-';
  }
  return `${normalized.slice(0, 4)}-${normalized.slice(4, 6)}-${normalized.slice(6, 8)}`;
}

function csvEscape(value) {
  if (value === null || value === undefined) {
    return '';
  }
  const text = String(value);
  if (!/[",\n]/.test(text)) {
    return text;
  }
  return `"${text.replace(/"/g, '""')}"`;
}

function normalizeIndexDate(value) {
  const digits = String(value || '').replace(/\D/g, '');
  if (digits.length !== 8) {
    return null;
  }
  return `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6, 8)}`;
}

function resolveCsvLatestDate(csvText) {
  const rows = parseCsv(csvText);
  return rows
    .map((row) => normalizeIndexDate(row.trade_date || row.date))
    .filter(Boolean)
    .sort()
    .at(-1) || null;
}

function parseRankedRows(csvText, label, limit) {
  const rows = parseCsv(csvText)
    .map((row) => ({
      ...row,
      code: normalizeCode(row.code),
      rank: toNumber(row.rank),
      score_100: toNumber(row.score_100),
      rps20: toNumber(row.rps20),
      rps90: toNumber(row.rps90),
      trade_date: normalizeTradeDate(row.trade_date),
    }))
    .filter((row) => row.code)
    .sort((left, right) => (left.rank || 0) - (right.rank || 0))
    .slice(0, limit);

  if (rows.length === 0) {
    throw new Error(`${label}榜单为空，无法生成综合榜`);
  }

  return rows;
}

function getSingleTradeDate(rows, label) {
  const values = [...new Set(rows.map((row) => row.trade_date).filter(Boolean))];
  if (values.length !== 1) {
    throw new Error(`${label}榜单 trade_date 不唯一，无法按“当天任意两榜重叠”生成综合榜`);
  }
  return values[0];
}

function formatScore(value) {
  return Number.isFinite(value) ? value.toFixed(2) : '-';
}

function buildCombinedMarkdown(items, generatedAt, tradeDate) {
  const lines = [
    '# 综合榜 Top 20',
    '',
    `- 生成时间: ${generatedAt}`,
    `- 交易日期: ${formatTradeDateLabel(tradeDate)}`,
    `- 入榜规则: ${COMBINED_RULE_TEXT}`,
    '- 排序规则: 综合分 = 命中榜单的 score_100 均值',
    '',
    '| 排名 | 代码 | 名称 | 行业 | 综合分 | 盘后分 | 龙头分 | RPS分 | RPS20 | RPS90 |',
    '|---:|---:|---|---|---:|---:|---:|---:|---:|---:|',
  ];

  items.forEach((item) => {
    lines.push(
      `| ${item.rank} | ${item.code} | ${item.name} | ${item.industry} | ${formatScore(item.score_100)} | ${formatScore(item.short_score_100)} | ${formatScore(item.leader_score_100)} | ${formatScore(item.rps90_score_100)} | ${formatScore(item.rps20)} | ${formatScore(item.rps90)} |`
    );
  });

  if (items.length === 0) {
    lines.push('| - | - | 当前无满足任意两榜条件的标的 | - | - | - | - | - | - | - |');
  }

  return `${lines.join('\n')}\n`;
}

async function readCombinedBoard() {
  const csvExists = existsSync(COMBINED_CURRENT_CSV_PATH);
  const mdExists = existsSync(COMBINED_CURRENT_MD_PATH);
  const exists = csvExists || mdExists;

  if (!exists) {
    return {
      exists: false,
      csvText: '',
      markdown: '',
      updatedAt: null,
      sourcePath: 'docs/list/combined_top20.csv',
    };
  }

  const [csvText, markdown] = await Promise.all([
    csvExists ? readFile(COMBINED_CURRENT_CSV_PATH, 'utf8') : Promise.resolve(''),
    mdExists ? readFile(COMBINED_CURRENT_MD_PATH, 'utf8') : Promise.resolve(''),
  ]);

  const latestPath = mdExists ? COMBINED_CURRENT_MD_PATH : COMBINED_CURRENT_CSV_PATH;
  return {
    exists: true,
    csvText,
    markdown,
    updatedAt: statSync(latestPath).mtime.toISOString(),
    sourcePath: 'docs/list/combined_top20.csv',
  };
}

async function generateCombinedBoard() {
  const [shortCsvText, leaderCsvText, rps90CsvText] = await Promise.all([
    readFile(resolveShortListPath('short_top20.csv'), 'utf8'),
    readFile(path.join(ROOT_DIR, 'docs', 'list', 'leader_top20.csv'), 'utf8'),
    readFile(path.join(ROOT_DIR, 'docs', 'list', 'rps90_top20.csv'), 'utf8'),
  ]);

  const shortRows = parseRankedRows(shortCsvText, '短线盘后版', 10);
  const leaderRows = parseRankedRows(leaderCsvText, '龙头抱团', 20);
  const rps90Rows = parseRankedRows(rps90CsvText, 'RPS双90', 20);

  const shortTradeDate = getSingleTradeDate(shortRows, '短线盘后版');
  const leaderTradeDate = getSingleTradeDate(leaderRows, '龙头抱团');
  const rps90TradeDate = getSingleTradeDate(rps90Rows, 'RPS双90');

  if (shortTradeDate !== leaderTradeDate || shortTradeDate !== rps90TradeDate) {
    throw new Error(
      `三榜交易日不一致：盘后=${formatTradeDateLabel(shortTradeDate)}，RPS=${formatTradeDateLabel(rps90TradeDate)}，龙头=${formatTradeDateLabel(leaderTradeDate)}`
    );
  }

  const shortByCode = new Map(shortRows.map((row) => [row.code, row]));
  const leaderByCode = new Map(leaderRows.map((row) => [row.code, row]));
  const rps90ByCode = new Map(rps90Rows.map((row) => [row.code, row]));

  const commonCodes = [...new Set([...shortRows, ...leaderRows, ...rps90Rows].map((row) => row.code))]
    .filter(
      (code) =>
        [shortByCode.has(code), leaderByCode.has(code), rps90ByCode.has(code)].filter(Boolean).length >= 2
    );

  const items = commonCodes
    .map((code) => {
      const shortRow = shortByCode.get(code);
      const leaderRow = leaderByCode.get(code);
      const rps90Row = rps90ByCode.get(code);
      const shortScore = toNumber(shortRow?.score_100);
      const leaderScore = toNumber(leaderRow?.score_100);
      const rpsScore = toNumber(rps90Row?.score_100);
      const validScores = [shortScore, leaderScore, rpsScore].filter((value) => value != null);
      const score = validScores.length > 0
        ? validScores.reduce((sum, value) => sum + value, 0) / validScores.length
        : null;

      return {
        rank: 0,
        code,
        name: shortRow?.name || leaderRow?.name || rps90Row?.name || code,
        industry: shortRow?.industry || leaderRow?.industry || rps90Row?.industry || '',
        score_100: score,
        short_score_100: shortScore,
        leader_score_100: leaderScore,
        rps90_score_100: rpsScore,
        short_rank: shortRow?.rank ?? null,
        leader_rank: leaderRow?.rank ?? null,
        rps_rank: rps90Row?.rank ?? null,
        rps20: toNumber(rps90Row?.rps20),
        rps90: toNumber(rps90Row?.rps90),
        trade_date: shortTradeDate,
      };
    })
    .sort((left, right) => {
      const scoreDiff = (right.score_100 || 0) - (left.score_100 || 0);
      if (scoreDiff !== 0) {
        return scoreDiff;
      }
      return (left.short_rank || 9999) - (right.short_rank || 9999);
    })
    .map((item, index) => ({ ...item, rank: index + 1 }));

  const headers = [
    'rank',
    'code',
    'name',
    'industry',
    'score_100',
    'short_score_100',
    'leader_score_100',
    'rps90_score_100',
    'short_rank',
    'leader_rank',
    'rps_rank',
    'rps20',
    'rps90',
    'trade_date',
  ];
  const csvText = [
    headers.join(','),
    ...items.map((item) => headers.map((header) => csvEscape(item[header])).join(',')),
  ].join('\n');

  const generatedAt = new Date().toISOString().slice(0, 19).replace('T', ' ');
  const markdown = buildCombinedMarkdown(items, generatedAt, shortTradeDate);
  const historyDir = path.join(ROOT_DIR, 'docs', 'list', 'history', 'combined', formatTradeDateLabel(shortTradeDate));

  await mkdir(historyDir, { recursive: true });
  await Promise.all([
    writeFile(COMBINED_CURRENT_CSV_PATH, `${csvText}\n`, 'utf8'),
    writeFile(COMBINED_CURRENT_MD_PATH, markdown, 'utf8'),
    writeFile(path.join(historyDir, 'combined_top20.csv'), `${csvText}\n`, 'utf8'),
    writeFile(path.join(historyDir, 'combined_top20.md'), markdown, 'utf8'),
  ]);

  return {
    exists: true,
    csvText: `${csvText}\n`,
    markdown,
    updatedAt: new Date().toISOString(),
    sourcePath: 'docs/list/combined_top20.csv',
    itemCount: items.length,
    tradeDate: shortTradeDate,
    rule: COMBINED_RULE_TEXT,
  };
}

function runScript(scriptPath, args = []) {
  return new Promise((resolve, reject) => {
    const pythonBin = DEFAULT_PYTHON_BIN;
    const output = [];
    const child = spawn(pythonBin, [scriptPath, ...args], {
      cwd: ROOT_DIR,
      env: {
        ...process.env,
        PYTHONUTF8: '1',
      },
    });

    const collect = (chunk) => {
      const lines = String(chunk || '')
        .replace(/\r/g, '')
        .split('\n')
        .filter(Boolean);
      output.push(...lines);
      if (output.length > 120) {
        output.splice(0, output.length - 120);
      }
    };

    child.stdout.on('data', collect);
    child.stderr.on('data', collect);
    child.on('error', (error) => {
      error.output = output;
      reject(error);
    });
    child.on('close', (code) => {
      if (code === 0) {
        resolve({ code, output, pythonBin });
        return;
      }
      const error = new Error(`${path.basename(scriptPath)} exited with ${code}`);
      error.output = output;
      error.code = code;
      reject(error);
    });
  });
}

async function refreshKechuangIndex() {
  const run = await runScript(KECHUANG_DOWNLOAD_SCRIPT_PATH);
  const csvText = await readFile(KECHUANG_INDEX_CSV_PATH, 'utf8');
  const latestTradeDate = resolveCsvLatestDate(csvText);

  if (!latestTradeDate) {
    throw new Error('科创指数日线为空，无法读取最新交易日');
  }

  return {
    ok: true,
    indexCode: KECHUANG_INDEX_CODE,
    csvText,
    latestTradeDate,
    updatedAt: statSync(KECHUANG_INDEX_CSV_PATH).mtime.toISOString(),
    output: run.output,
    pythonBin: run.pythonBin,
  };
}

async function readIndustryTrendRank() {
  if (!existsSync(INDUSTRY_TREND_RANK_PATH)) {
    return { exists: false, industries: [], updatedAt: null, tradeDate: null };
  }

  const payload = JSON.parse(await readFile(INDUSTRY_TREND_RANK_PATH, 'utf8'));
  return {
    exists: true,
    ...payload,
    updatedAt: payload.updatedAt || statSync(INDUSTRY_TREND_RANK_PATH).mtime.toISOString(),
  };
}

async function refreshIndustryTrendRank() {
  const run = await runScript(INDUSTRY_TREND_SCRIPT_PATH, ['--output', INDUSTRY_TREND_RANK_PATH]);
  const payload = await readIndustryTrendRank();
  return { ...payload, output: run.output, pythonBin: run.pythonBin };
}

function appendOutput(state, chunk) {
  const text = String(chunk || '').replace(/\r/g, '');
  const lines = text.split('\n').filter(Boolean);
  if (!lines.length) {
    return;
  }
  const visibleLines = [];
  lines.forEach((line) => {
    if (line.startsWith(SETTLEMENT_SUMMARY_PREFIX)) {
      try {
        state.settlementSummary = JSON.parse(line.slice(SETTLEMENT_SUMMARY_PREFIX.length));
      } catch (error) {
        visibleLines.push(`[settlement-summary-parse-error] ${error.message}`);
      }
      return;
    }
    visibleLines.push(line);
  });

  state.output.push(...visibleLines);
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
  const { csvPath, mdPath } = resolveTop5Paths(job);
  const csvExists = existsSync(csvPath);
  const mdExists = existsSync(mdPath);
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
    csvExists ? readFile(csvPath, 'utf8') : Promise.resolve(''),
    mdExists ? readFile(mdPath, 'utf8') : Promise.resolve(''),
  ]);

  const latestPath = mdExists ? mdPath : csvPath;
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

  const pythonBin = DEFAULT_PYTHON_BIN;
  state.status = 'running';
  state.running = true;
  state.startedAt = new Date().toISOString();
  state.finishedAt = null;
  state.exitCode = null;
  state.output = [`[start] ${job.label}`, `[python] ${pythonBin}`, `[script] ${path.relative(ROOT_DIR, job.scriptPath)}`];
  state.pythonBin = pythonBin;
  state.settlementSummary = null;

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

  const jobStatusMatch = url.pathname.match(/^\/api\/jobs\/(postclose|tail|rps90|leader)$/);
  if (req.method === 'GET' && jobStatusMatch) {
    const key = jobStatusMatch[1];
    json(res, 200, snapshotState(key));
    return;
  }

  const jobRunMatch = url.pathname.match(/^\/api\/run\/(postclose|tail|rps90|leader)$/);
  if (req.method === 'POST' && jobRunMatch) {
    const key = jobRunMatch[1];
    const result = startJob(key);
    json(res, result.ok ? 202 : 409, result);
    return;
  }

  const top5Match = url.pathname.match(/^\/api\/top5\/(postclose|tail|rps90|leader)$/);
  if (req.method === 'GET' && top5Match) {
    try {
      const payload = await readTop5(top5Match[1]);
      json(res, 200, payload);
    } catch (error) {
      json(res, 500, { error: error.message || 'read-top5-failed' });
    }
    return;
  }

  if (req.method === 'GET' && url.pathname === '/api/combined') {
    try {
      const payload = await readCombinedBoard();
      json(res, 200, payload);
    } catch (error) {
      json(res, 500, { error: error.message || 'read-combined-failed' });
    }
    return;
  }

  if (req.method === 'POST' && url.pathname === '/api/combined/generate') {
    try {
      const payload = await generateCombinedBoard();
      json(res, 200, payload);
    } catch (error) {
      json(res, 500, { error: error.message || 'generate-combined-failed' });
    }
    return;
  }

  if (req.method === 'POST' && url.pathname === '/api/kechuang/refresh') {
    try {
      const payload = await refreshKechuangIndex();
      json(res, 200, payload);
    } catch (error) {
      json(res, 500, {
        error: error.message || 'refresh-kechuang-failed',
        output: Array.isArray(error.output) ? error.output : [],
      });
    }
    return;
  }

  if (req.method === 'GET' && url.pathname === '/api/industry-trends') {
    try {
      json(res, 200, await readIndustryTrendRank());
    } catch (error) {
      json(res, 500, { error: error.message || 'read-industry-trends-failed' });
    }
    return;
  }

  if (req.method === 'POST' && url.pathname === '/api/industry-trends/refresh') {
    try {
      json(res, 200, await refreshIndustryTrendRank());
    } catch (error) {
      json(res, 500, {
        error: error.message || 'refresh-industry-trends-failed',
        output: Array.isArray(error.output) ? error.output : [],
      });
    }
    return;
  }

  json(res, 404, { error: 'not-found' });
});

server.listen(PORT, HOST, () => {
  console.log(`[ops-api] http://${HOST}:${PORT}`);
});
