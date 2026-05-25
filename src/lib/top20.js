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

const shortTop20MarkdownHistoryModules = import.meta.glob('../../docs/list/history/short/*/short_top20.md', {
  eager: true,
  query: '?raw',
  import: 'default',
});

const shortSummaryHistoryModules = import.meta.glob('../../docs/list/history/short/*/short_summary.md', {
  eager: true,
  query: '?raw',
  import: 'default',
});

function normalizeModuleSourcePath(modulePath) {
  return String(modulePath || '').replace(/^\.\.\/\.\.\//, '');
}

function extractShortHistoryDate(modulePath) {
  const matched = String(modulePath || '').match(/history\/short\/(\d{4}-\d{2}-\d{2})\//);
  return matched ? matched[1] : null;
}

function selectLatestShortHistoryModule(modules) {
  const entries = Object.entries(modules || {})
    .map(([modulePath, text]) => ({
      date: extractShortHistoryDate(modulePath),
      sourcePath: normalizeModuleSourcePath(modulePath),
      text,
    }))
    .filter((entry) => entry.date && typeof entry.text === 'string')
    .sort((a, b) => String(a.date).localeCompare(String(b.date)));

  return entries.at(-1) || null;
}

const latestShortTop20MarkdownHistory = selectLatestShortHistoryModule(shortTop20MarkdownHistoryModules);
const latestShortSummaryHistory = selectLatestShortHistoryModule(shortSummaryHistoryModules);

const shortTop20HistoryModules = import.meta.glob('../../docs/list/history/short/*/short_top20.csv', {
  eager: true,
  query: '?raw',
  import: 'default',
});
const latestShortTop20History = selectLatestShortHistoryModule(shortTop20HistoryModules);

const leaderTop20HistoryModules = import.meta.glob('../../docs/list/history/leader/*/leader_top20.csv', {
  eager: true,
  query: '?raw',
  import: 'default',
});

const leaderSummaryHistoryModules = import.meta.glob('../../docs/list/history/leader/*/leader_summary.md', {
  eager: true,
  query: '?raw',
  import: 'default',
});

function extractLeaderHistoryDate(modulePath) {
  const matched = String(modulePath || '').match(/history\/leader\/(\d{4}-\d{2}-\d{2}|\d{8})\//);
  return matched ? matched[1] : null;
}

function selectLatestLeaderHistoryModule(modules) {
  const entries = Object.entries(modules || {})
    .map(([modulePath, text]) => ({
      date: extractLeaderHistoryDate(modulePath),
      sourcePath: normalizeModuleSourcePath(modulePath),
      text,
    }))
    .filter((entry) => entry.date && typeof entry.text === 'string')
    .sort((a, b) => String(a.date).localeCompare(String(b.date)));

  return entries.at(-1) || null;
}

const latestLeaderTop20History = selectLatestLeaderHistoryModule(leaderTop20HistoryModules);
const latestLeaderSummaryHistory = selectLatestLeaderHistoryModule(leaderSummaryHistoryModules);

const rps90Top20HistoryModules = import.meta.glob('../../docs/list/history/rps90/*/rps90_top20.csv', {
  eager: true,
  query: '?raw',
  import: 'default',
});

const rps90SummaryHistoryModules = import.meta.glob('../../docs/list/history/rps90/*/rps90_summary.md', {
  eager: true,
  query: '?raw',
  import: 'default',
});

function extractRps90HistoryDate(modulePath) {
  const matched = String(modulePath || '').match(/history\/rps90\/(\d{4}-\d{2}-\d{2}|\d{8})\//);
  return matched ? matched[1] : null;
}

function selectLatestRps90HistoryModule(modules) {
  const entries = Object.entries(modules || {})
    .map(([modulePath, text]) => ({
      date: extractRps90HistoryDate(modulePath),
      sourcePath: normalizeModuleSourcePath(modulePath),
      text,
    }))
    .filter((entry) => entry.date && typeof entry.text === 'string')
    .sort((a, b) => String(a.date).localeCompare(String(b.date)));

  return entries.at(-1) || null;
}

const latestRps90Top20History = selectLatestRps90HistoryModule(rps90Top20HistoryModules);
const latestRps90SummaryHistory = selectLatestRps90HistoryModule(rps90SummaryHistoryModules);

export const FACTOR_DEFINITIONS = {
  short: {
    key: 'short',
    title: '短线多因子-盘后版',
    subtitle: '查看盘后Top10候选，并按记录日期回看榜单与新增/移除变化。',
    description: '聚焦次日可交易的短期启动、活跃度、单票过滤与流动性，目标持有后续2-3个交易日。',
    riskNote: '当前后端主要做单票层过滤与打分，暂未加入指数环境过滤、市场风格切换、行业集中度约束和组合层仓位控制。Top10 更适合作为候选池参考，不等同于可直接等权执行的组合。',
    displayLimit: 10,
    emptyMessage: '未找到 `short_top20*.csv` 数据文件。',
    current: {
      sourcePath: latestShortTop20History?.sourcePath || 'docs/list/short_top20.csv',
      csvText: latestShortTop20History?.text || shortTop20Text,
      metaTexts: [
        latestShortTop20MarkdownHistory?.text || shortTop20Markdown,
        latestShortSummaryHistory?.text || shortSummaryText,
      ],
    },
  },
  tail: {
    key: 'tail',
    title: '短线多因子-尾盘版',
    subtitle: '查看尾盘Top10候选，并按记录日期回看榜单与新增/移除变化。',
    description: '使用尾盘版独立权重与过滤器，偏重当日量能爆发和短期启动，输出尾盘场景候选。',
    riskNote: '当前后端主要做单票层过滤与打分，暂未加入指数环境过滤、市场风格切换、行业集中度约束和组合层仓位控制。尾盘 Top10 可能出现同题材或同风格集中，适合作为候选池参考。',
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
      sourcePath: latestLeaderTop20History?.sourcePath || 'docs/list/leader_top20.csv',
      csvText: latestLeaderTop20History?.text || leaderTop20Text,
      metaTexts: [latestLeaderSummaryHistory?.text || leaderSummaryText],
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
      sourcePath: latestRps90Top20History?.sourcePath || 'docs/list/rps90_top20.csv',
      csvText: latestRps90Top20History?.text || rps90Top20Text,
      metaTexts: [latestRps90SummaryHistory?.text || rps90SummaryText],
    },
  },
  combined: {
    key: 'combined',
    type: 'combined',
    title: '综合榜单',
    subtitle: '同时入选龙头抱团Top20、盘后版Top10、RPS双90 Top20的交集标的。',
    description: '三种策略共同认可：龙头抱团前20 ∩ 盘后版评分前10 ∩ RPS双90前20。综合分为三策略 score_100 均均。',
    riskNote: '综合榜单仅反映当日多策略共识，不构成买入建议。交集产出候选数量可能较少，甚至为空。',
    emptyMessage: '当前无股票同时满足三个策略条件。',
    sources: {
      short: { csvText: latestShortTop20History?.text || shortTop20Text, limit: 10 },
      leader: { csvText: latestLeaderTop20History?.text || leaderTop20Text, limit: 20 },
      rps90: { csvText: latestRps90Top20History?.text || rps90Top20Text, limit: 20 },
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
    date: snapshot.date || generatedAt || latestTradeDate || latestNoticeDate || reportDate || sourcePath,
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

  const commonCodes = shortItems
    .map((i) => i.code)
    .filter((c) => leaderByCode.has(c) && rps90ByCode.has(c));

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

  return buildSnapshotBase(items, { generatedAt }, definition.current.sourcePath);
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

export async function loadFactorSnapshots(factorKey) {
  const definition = getFactorDefinition(factorKey);

  if (definition.type === 'combined') {
    const snapshot = buildCombinedSnapshot(definition);
    return snapshot ? [snapshot] : [];
  }
  const snapshots = [];

  getHistoryList(definition.key).forEach((snapshot, index) => {
    const historySnapshot = buildHistorySnapshot(snapshot, `history-${definition.key}-${index + 1}`);
    if (historySnapshot) {
      snapshots.push(historySnapshot);
    }
  });

  const currentSnapshot = buildCurrentSnapshot(definition);
  if (currentSnapshot) {
    snapshots.push(currentSnapshot);
  }

  const dedupedMap = new Map();
  snapshots.forEach((snapshot) => {
    dedupedMap.set(snapshot.date, snapshot);
  });

  return Array.from(dedupedMap.values()).sort(sortSnapshotsByDate);
}
