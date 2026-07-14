import { parseCsv } from './csv';
import shortSummaryText from '../../docs/list/short_summary.md?raw';
import shortTop20Text from '../../docs/list/short_top20.csv?raw';
import shortTop20Markdown from '../../docs/list/short_top20.md?raw';
import tailSummaryText from '../../docs/list/tail_summary.md?raw';
import tailTop20Text from '../../docs/list/tail_top20.csv?raw';
import tailTop20Markdown from '../../docs/list/tail_top20.md?raw';
import leaderTop20Text from '../../docs/list/leader_top20.csv?raw';
import leaderSummaryText from '../../docs/list/leader_summary.md?raw';
import rps90Top20Text from '../../docs/list/rps90_top20.csv?raw';
import rps90SummaryText from '../../docs/list/rps90_summary.md?raw';
import historySeed from '../data/top20_history.json';

const HISTORY_MODULES = {
  short: {
    csv: import.meta.glob('../../docs/list/history/short/*/short_top20.csv', {
      query: '?raw',
      import: 'default',
    }),
  },
  leader: {
    csv: import.meta.glob('../../docs/list/history/leader/*/leader_top20.csv', {
      query: '?raw',
      import: 'default',
    }),
  },
  rps90: {
    csv: import.meta.glob('../../docs/list/history/rps90/*/rps90_top20.csv', {
      query: '?raw',
      import: 'default',
    }),
  },
};

function normalizeModuleSourcePath(modulePath) {
  return String(modulePath || '').replace(/^\.\.\/\.\.\//, '');
}

function normalizeSnapshotDate(value) {
  const digits = String(value || '').replace(/\D/g, '');
  if (digits.length === 8) {
    return `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6, 8)}`;
  }
  return String(value || '').trim() || null;
}

function extractHistoryDate(factorKey, modulePath) {
  const factorPattern = factorKey === 'short' ? 'short' : factorKey;
  const matched = String(modulePath || '').match(
    new RegExp(`history/${factorPattern}/(\\d{4}-\\d{2}-\\d{2}|\\d{8})/`)
  );
  return matched ? normalizeSnapshotDate(matched[1]) : null;
}

function listHistoryEntries(factorKey) {
  const modules = HISTORY_MODULES[factorKey];
  if (!modules) {
    return [];
  }

  return Object.entries(modules.csv)
    .map(([modulePath, loadCsv]) => {
      const date = extractHistoryDate(factorKey, modulePath);
      if (!date) {
        return null;
      }
      return {
        id: `${factorKey}:history:${modulePath}`,
        date,
        sourcePath: normalizeModuleSourcePath(modulePath),
        loadCsv,
      };
    })
    .filter(Boolean)
    .sort((left, right) => left.date.localeCompare(right.date));
}

function getHistoryEntry(factorKey, id) {
  return listHistoryEntries(factorKey).find((entry) => entry.id === id) || null;
}

export const FACTOR_DEFINITIONS = {
  short: {
    key: 'short',
    title: '短线三日上涨概率-盘后版',
    subtitle: '查看盘后三日上涨概率Top10候选，并按记录日期回看榜单与新增/移除变化。',
    description: '盘后使用三日上涨概率模型估计未来3个交易日收盘上涨可能性，输出概率最高的候选。',
    riskNote: '盘后版已启用市场环境闸门与单行业候选上限。Top10 仍是评分候选，只有通过交易过滤且市场闸门放行的标的才可进入执行清单。',
    displayLimit: 10,
    emptyMessage: '未找到 `short_top20*.csv` 数据文件。',
    current: {
      sourcePath: 'docs/list/short_top20.csv',
      csvText: shortTop20Text,
      metaTexts: [shortTop20Markdown, shortSummaryText],
    },
  },
  tail: {
    key: 'tail',
    title: '短线三日上涨概率-收盘资金版',
    subtitle: '查看收盘资金三日上涨概率Top10候选，并按记录日期回看榜单与新增/移除变化。',
    description: '使用收盘价、成交量和资金流入流出估计未来3个交易日收盘上涨可能性，输出概率最高的候选。',
    riskNote: '收盘资金版目前仍以单票层概率排序为主，未设置指数环境闸门与行业集中度约束。Top10 可能集中于同一题材或风格，适合作为候选池参考。',
    displayLimit: 10,
    emptyMessage: '未找到 `tail_top20*.csv` 数据文件。',
    current: {
      sourcePath: 'docs/list/tail_top20.csv',
      csvText: tailTop20Text,
      metaTexts: [tailTop20Markdown, tailSummaryText],
    },
  },
  leader: {
    key: 'leader',
    title: '龙头抱团-盘后版',
    subtitle: '查看龙头抱团Top20候选，聚焦行业领涨龙头与机构抱团特征。',
    description: '筛选行业内持续领先（20/60日收益率行业排名≥70%）且具备机构抱团特征（持续净流入、低波动上涨、均线多头）的中期标的，目标持有5-15个交易日。',
    riskNote: '龙头抱团策略面向中期趋势持仓，当前未加入指数环境过滤、行业集中度约束和组合层仓位控制。Top20 应作为候选池参考，建议结合市场整体趋势研判后操作。',
    emptyMessage: '未找到 `leader_top20*.csv` 数据文件。',
    current: {
      sourcePath: 'docs/list/leader_top20.csv',
      csvText: leaderTop20Text,
      metaTexts: [leaderSummaryText],
    },
  },
  rps90: {
    key: 'rps90',
    title: 'RPS双90-盘后版',
    subtitle: '查看RPS双90 Top20候选，聚焦20日和90日相对强度均≥90的强势股。',
    description: '筛选20日RPS≥90且90日RPS≥90的高相对强度股票，按综合评分排名，展示前20支。',
    riskNote: 'RPS策略聚焦强者恒强动量效应，未加入指数环境过滤和组合层控制。Top20 适合作为趋势跟踪候选池，高RPS股票追高风险较大，建议结合个股K线形态判断。',
    displayLimit: 20,
    emptyMessage: '未找到 `rps90_top20*.csv` 数据文件。',
    current: {
      sourcePath: 'docs/list/rps90_top20.csv',
      csvText: rps90Top20Text,
      metaTexts: [rps90SummaryText],
    },
  },
  combined: {
    key: 'combined',
    type: 'combined',
    title: '综合榜单',
    subtitle: '入选龙头抱团Top20、盘后概率Top10、RPS双90 Top20中任意两个榜单的重叠标的。',
    description: '三种策略两两共识：至少同时出现在龙头抱团前20、盘后三日上涨概率前10、RPS双90前20中的两个榜单。综合分为命中榜单 score_100 的均值。',
    riskNote: '综合榜单仅反映当日多策略共识，不构成买入建议。两榜重叠产出候选数量仍可能较少。',
    emptyMessage: '当前无股票同时满足任意两个策略条件。',
    sources: {
      short: { csvText: shortTop20Text, limit: 10 },
      leader: { csvText: leaderTop20Text, limit: 20 },
      rps90: { csvText: rps90Top20Text, limit: 20 },
    },
  },
};

function toNumber(value) {
  if (value === null || value === undefined || value === '') {
    return null;
  }

  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function readValue(row, keys, fallback = '') {
  for (const key of keys) {
    const value = row?.[key];
    if (value !== undefined && value !== null && value !== '') {
      return value;
    }
  }
  return fallback;
}

function toBoolean(value) {
  if (value === true || value === 'true' || value === 'True' || value === '1') {
    return true;
  }
  return false;
}

function toOptionalBoolean(value) {
  if (value === null || value === undefined || value === '') {
    return null;
  }
  return toBoolean(value);
}

function normalizeCode(code) {
  if (!code) {
    return '';
  }
  return String(code).padStart(6, '0');
}

function normalizeRow(row = {}) {
  return {
    rank: toNumber(readValue(row, ['rank'])),
    shortRank: toNumber(readValue(row, ['shortRank', 'short_rank'])),
    leaderRank: toNumber(readValue(row, ['leaderRank', 'leader_rank'])),
    rpsRank: toNumber(readValue(row, ['rpsRank', 'rps_rank'])),
    code: normalizeCode(readValue(row, ['code'])),
    name: readValue(row, ['name']),
    industry: readValue(row, ['industry']),
    score100: toNumber(readValue(row, ['score100', 'score_100'])),
    shortScore100: toNumber(readValue(row, ['shortScore100', 'short_score_100'])),
    leaderScore100: toNumber(readValue(row, ['leaderScore100', 'leader_score_100'])),
    rps90Score100: toNumber(readValue(row, ['rps90Score100', 'rps90_score_100'])),
    scoreRaw: toNumber(readValue(row, ['scoreRaw', 'score_raw'])),
    valueScore: toNumber(readValue(row, ['valueScore', 'value_score'])),
    qualityScore: toNumber(readValue(row, ['qualityScore', 'quality_score'])),
    growthScore: toNumber(readValue(row, ['growthScore', 'growth_score'])),
    launchScore: toNumber(readValue(row, ['launchScore', 'launch_score', 'leadership_score'])),
    trendScore: toNumber(readValue(row, ['trendScore', 'trend_score'])),
    momentumScore: toNumber(readValue(row, ['momentumScore', 'momentum_score'])),
    lowvolScore: toNumber(readValue(row, ['lowvolScore', 'lowvol_score'])),
    activityScore: toNumber(readValue(row, ['activityScore', 'activity_score', 'cluster_score'])),
    stabilityScore: toNumber(readValue(row, ['stabilityScore', 'stability_score'])),
    liquidityScore: toNumber(readValue(row, ['liquidityScore', 'liquidity_score'])),
    upProb3d: toNumber(readValue(row, ['upProb3d', 'up_prob_3d'])),
    expectedRet3d: toNumber(readValue(row, ['expectedRet3d', 'expected_ret_3d'])),
    upProb3dConfidence: toNumber(readValue(row, ['upProb3dConfidence', 'up_prob_3d_confidence'])),
    passMarketEnv: toOptionalBoolean(readValue(row, ['passMarketEnv', 'pass_market_env'], null)),
    quoteOnlyFallbackUsed: toBoolean(readValue(row, ['quoteOnlyFallbackUsed', 'quote_only_fallback_used'])),
    klineFallbackUsed: toBoolean(readValue(row, ['klineFallbackUsed', 'kline_fallback_used'])),
    reportDate: readValue(row, ['reportDate', 'report_date']),
    noticeDate: readValue(row, ['noticeDate', 'notice_date']),
    tradeDate: readValue(row, ['tradeDate', 'trade_date']),
    rpsScore: toNumber(readValue(row, ['rpsScore', 'rps_score'])),
    rps20: toNumber(readValue(row, ['rps20'])),
    rps90: toNumber(readValue(row, ['rps90'])),
    ret20dPct: toNumber(readValue(row, ['ret20dPct', 'ret_20d_pct'])),
    ret90dPct: toNumber(readValue(row, ['ret90dPct', 'ret_90d_pct'])),
    closePx: toNumber(readValue(row, ['closePx', 'close_today', 'close'])),
    amountToday: toNumber(readValue(row, ['amountToday', 'amount_today'])),
  };
}

function extractGeneratedAt(markdown) {
  const matched = markdown?.match(/^- 生成时间:\s*(.+)$/m);
  return matched ? matched[1].trim() : null;
}

function latestDateValue(items, key) {
  return (
    items
      .map((item) => item[key])
      .filter(Boolean)
      .sort()
      .slice(-1)[0] || null
  );
}

function parseDateValue(value) {
  if (!value) {
    return null;
  }

  const stringValue = String(value);
  const normalized = stringValue.includes(' ') ? stringValue.replace(' ', 'T') : `${stringValue}T00:00:00`;
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function sortSnapshotsByDate(a, b) {
  const parsedA = parseDateValue(a.date);
  const parsedB = parseDateValue(b.date);

  if (parsedA && parsedB) {
    return parsedA.getTime() - parsedB.getTime();
  }

  if (parsedA) {
    return 1;
  }

  if (parsedB) {
    return -1;
  }

  return String(a.date).localeCompare(String(b.date));
}

function buildSnapshotBase(items, snapshot, sourcePath) {
  if (items.length === 0) {
    return null;
  }

  const reportDate = snapshot.reportDate || latestDateValue(items, 'reportDate');
  const latestNoticeDate = snapshot.latestNoticeDate || latestDateValue(items, 'noticeDate');
  const latestTradeDate = snapshot.latestTradeDate || latestDateValue(items, 'tradeDate');
  const generatedAt = snapshot.generatedAt || null;

  return {
    date: normalizeSnapshotDate(snapshot.date || latestTradeDate || latestNoticeDate || reportDate) || generatedAt || sourcePath,
    sourcePath,
    items,
    generatedAt,
    reportDate,
    latestNoticeDate,
    latestTradeDate,
  };
}

function buildCombinedSnapshot(definition) {
  const { sources } = definition;

  function parseSource(src) {
    return parseCsv(src.csvText)
      .map(normalizeRow)
      .sort((a, b) => (a.rank || 0) - (b.rank || 0))
      .slice(0, src.limit || Infinity);
  }

  const shortItems = parseSource(sources.short);
  const leaderItems = parseSource(sources.leader);
  const rps90Items = parseSource(sources.rps90);

  const shortByCode = new Map(shortItems.map((i) => [i.code, i]));
  const leaderByCode = new Map(leaderItems.map((i) => [i.code, i]));
  const rps90ByCode = new Map(rps90Items.map((i) => [i.code, i]));

  const commonCodes = [...new Set([...shortItems, ...leaderItems, ...rps90Items].map((item) => item.code))]
    .filter(
      (code) =>
        [shortByCode.has(code), leaderByCode.has(code), rps90ByCode.has(code)].filter(Boolean).length >= 2
    );

  if (commonCodes.length === 0) return null;

  const items = commonCodes
    .map((code) => {
      const s = shortByCode.get(code);
      const l = leaderByCode.get(code);
      const r = rps90ByCode.get(code);

      const shortScore = s?.score100 ?? null;
      const leaderScore = l?.score100 ?? null;
      const rpsScore = r?.score100 ?? null;
      const validScores = [shortScore, leaderScore, rpsScore].filter((v) => v != null);
      const combinedScore = validScores.length > 0
        ? validScores.reduce((a, b) => a + b, 0) / validScores.length
        : null;

      return {
        code,
        name: s?.name || l?.name || r?.name || code,
        industry: s?.industry || l?.industry || r?.industry || '',
        tradeDate: s?.tradeDate || l?.tradeDate || r?.tradeDate || null,
        score100: combinedScore,
        shortScore100: shortScore,
        leaderScore100: leaderScore,
        rps90Score100: rpsScore,
        rps20: r?.rps20 ?? null,
        rps90: r?.rps90 ?? null,
        closePx: s?.closePx || r?.closePx || null,
        quoteOnlyFallbackUsed: false,
        klineFallbackUsed: false,
      };
    })
    .sort((a, b) => (b.score100 || 0) - (a.score100 || 0))
    .map((item, idx) => ({ ...item, rank: idx + 1 }));

  return buildSnapshotBase(items, {}, 'combined');
}

export function buildCombinedSnapshotFromPayload(payload) {
  if (!payload?.exists) {
    return null;
  }

  const items = parseCsv(payload.csvText)
    .map(normalizeRow)
    .sort((a, b) => (a.rank || 0) - (b.rank || 0));

  if (items.length === 0) {
    return null;
  }

  const generatedAt = extractGeneratedAt(payload.markdown) || payload.updatedAt || null;
  return buildSnapshotBase(items, { generatedAt }, payload.sourcePath || 'docs/list/combined_top20.csv');
}

function buildCurrentSnapshot(definition) {
  const displayLimit = definition.displayLimit || Infinity;
  const items = parseCsv(definition.current.csvText)
    .map(normalizeRow)
    .sort((a, b) => (a.rank || 0) - (b.rank || 0))
    .slice(0, displayLimit);

  const generatedAt =
    definition.current.metaTexts
      .map((text) => extractGeneratedAt(text))
      .find(Boolean) || null;

  return buildSnapshotBase(
    items,
    {
      date: normalizeSnapshotDate(latestDateValue(items, 'tradeDate')),
      generatedAt,
    },
    definition.current.sourcePath
  );
}

function buildHistorySnapshot(snapshot, sourcePath) {
  const items = Array.isArray(snapshot.items)
    ? snapshot.items.map(normalizeRow).sort((a, b) => (a.rank || 0) - (b.rank || 0))
    : [];

  return buildSnapshotBase(items, snapshot, sourcePath);
}

function getHistoryList(factorKey) {
  return Array.isArray(historySeed?.[factorKey]) ? historySeed[factorKey] : [];
}

export function getFactorDefinition(factorKey) {
  return FACTOR_DEFINITIONS[factorKey] || FACTOR_DEFINITIONS.short;
}

export async function loadFactorHistorySnapshot(factorKey, id) {
  const definition = getFactorDefinition(factorKey);
  const entry = getHistoryEntry(definition.key, id);
  if (!entry) {
    throw new Error('未找到所选历史榜单。');
  }

  const csvText = await entry.loadCsv();
  const items = parseCsv(csvText)
    .map(normalizeRow)
    .sort((a, b) => (a.rank || 0) - (b.rank || 0))
    .slice(0, definition.displayLimit || Infinity);
  const snapshot = buildSnapshotBase(
    items,
    {
      date: entry.date,
    },
    entry.sourcePath
  );

  return snapshot ? { ...snapshot, id: entry.id, isCurrent: false } : null;
}

export async function loadFactorSnapshots(factorKey) {
  const definition = getFactorDefinition(factorKey);

  if (definition.type === 'combined') {
    const snapshot = buildCombinedSnapshot(definition);
    return {
      snapshots: snapshot ? [{ ...snapshot, id: 'combined:current', isCurrent: true }] : [],
      historyEntries: [],
    };
  }
  const snapshots = [];

  getHistoryList(definition.key).forEach((snapshot, index) => {
    const historySnapshot = buildHistorySnapshot(snapshot, `history-${definition.key}-${index + 1}`);
    if (historySnapshot) {
      snapshots.push({ ...historySnapshot, id: `${definition.key}:seed:${index + 1}`, isCurrent: false });
    }
  });

  const currentSnapshot = buildCurrentSnapshot(definition);
  if (currentSnapshot) {
    snapshots.push({ ...currentSnapshot, id: `${definition.key}:current`, isCurrent: true });
  }

  const dedupedMap = new Map();
  snapshots.forEach((snapshot) => {
    dedupedMap.set(snapshot.date, snapshot);
  });

  const currentDate = currentSnapshot?.date || null;
  const historyEntries = listHistoryEntries(definition.key).filter((entry) => entry.date !== currentDate);

  return {
    snapshots: Array.from(dedupedMap.values()).sort(sortSnapshotsByDate),
    historyEntries,
  };
}
