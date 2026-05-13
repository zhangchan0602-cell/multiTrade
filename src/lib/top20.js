import { parseCsv } from './csv';
import shortTop5Text from '../../docs/list/all_a_no_star_short_multifactor_top5.csv?raw';
import shortTop5Markdown from '../../docs/list/all_a_no_star_short_multifactor_top5.md?raw';
import shortSummaryText from '../../docs/list/all_a_no_star_short_multifactor_summary.md?raw';
import midTop20Text from '../../docs/list/all_a_no_star_mid_multifactor_top20.csv?raw';
import midTop20Markdown from '../../docs/list/all_a_no_star_mid_multifactor_top20.md?raw';
import myPlanText from '../../myplan.md?raw';
import historySeed from '../data/top20_history.json';

export const FACTOR_DEFINITIONS = {
  short: {
    key: 'short',
    title: '短线多因子',
    subtitle: '查看盘后Top5候选，并按记录日期回看榜单与新增/移除变化。',
    description: '聚焦次日可交易的短期启动、活跃度、风险控制与流动性，目标持有后续2-3个交易日。',
    emptyMessage: '未找到 `all_a_no_star_short_multifactor_top5*.csv` 数据文件。',
    current: {
      sourcePath: 'docs/list/all_a_no_star_short_multifactor_top5.csv',
      csvText: shortTop5Text,
      metaTexts: [shortTop5Markdown, shortSummaryText],
    },
  },
  mid: {
    key: 'mid',
    title: '中线多因子',
    subtitle: '查看最新 Top20，并按记录日期回看榜单与新增/移除变化。',
    description: '聚焦价值、质量、成长、动量与低波，适合做中线候选池与跟踪观察。',
    emptyMessage: '未找到 `all_a_no_star_mid_multifactor_top20*.csv` 数据文件。',
    current: {
      sourcePath: 'docs/list/all_a_no_star_mid_multifactor_top20.csv',
      csvText: midTop20Text,
      metaTexts: [myPlanText, midTop20Markdown],
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
    code: normalizeCode(readValue(row, ['code'])),
    name: readValue(row, ['name']),
    industry: readValue(row, ['industry']),
    score100: toNumber(readValue(row, ['score100', 'score_100'])),
    scoreRaw: toNumber(readValue(row, ['scoreRaw', 'score_raw'])),
    valueScore: toNumber(readValue(row, ['valueScore', 'value_score'])),
    qualityScore: toNumber(readValue(row, ['qualityScore', 'quality_score'])),
    growthScore: toNumber(readValue(row, ['growthScore', 'growth_score'])),
    launchScore: toNumber(readValue(row, ['launchScore', 'launch_score'])),
    trendScore: toNumber(readValue(row, ['trendScore', 'trend_score'])),
    momentumScore: toNumber(readValue(row, ['momentumScore', 'momentum_score'])),
    lowvolScore: toNumber(readValue(row, ['lowvolScore', 'lowvol_score'])),
    activityScore: toNumber(readValue(row, ['activityScore', 'activity_score'])),
    stabilityScore: toNumber(readValue(row, ['stabilityScore', 'stability_score'])),
    liquidityScore: toNumber(readValue(row, ['liquidityScore', 'liquidity_score'])),
    quoteOnlyFallbackUsed: toBoolean(readValue(row, ['quoteOnlyFallbackUsed', 'quote_only_fallback_used'])),
    klineFallbackUsed: toBoolean(readValue(row, ['klineFallbackUsed', 'kline_fallback_used'])),
    reportDate: readValue(row, ['reportDate', 'report_date']),
    noticeDate: readValue(row, ['noticeDate', 'notice_date']),
    tradeDate: readValue(row, ['tradeDate', 'trade_date']),
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

function buildCurrentSnapshot(definition) {
  const items = parseCsv(definition.current.csvText)
    .map(normalizeRow)
    .sort((a, b) => (a.rank || 0) - (b.rank || 0));

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
  if (Array.isArray(historySeed)) {
    return factorKey === 'mid' ? historySeed : [];
  }

  return Array.isArray(historySeed?.[factorKey]) ? historySeed[factorKey] : [];
}

export function getFactorDefinition(factorKey) {
  return FACTOR_DEFINITIONS[factorKey] || FACTOR_DEFINITIONS.short;
}

export async function loadFactorSnapshots(factorKey) {
  const definition = getFactorDefinition(factorKey);
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
