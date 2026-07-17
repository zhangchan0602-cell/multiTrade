import { parseCsv } from './csv';

const INDEX_CODE = '000688.SH';
const INDEX_NAME = '科创50';
const DRAWDOWN_HORIZONS = [3, 5, 10];
const UPSIDE_HORIZONS = [1, 3, 5, 10];
const MIN_ANALOG_COUNT = 24;
const MAX_ANALOG_COUNT = 56;

function toNumber(value) {
  if (value === null || value === undefined || value === '') {
    return null;
  }
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function normalizeDate(value) {
  const text = String(value || '').trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    return text;
  }
  const digits = text.replace(/\D/g, '');
  if (digits.length === 8) {
    return `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6, 8)}`;
  }
  return '';
}

function median(values) {
  return percentile(values, 0.5);
}

function percentile(values, ratio) {
  const nums = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (nums.length === 0) {
    return null;
  }
  const index = (nums.length - 1) * ratio;
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  if (lower === upper) {
    return nums[lower];
  }
  return nums[lower] + (nums[upper] - nums[lower]) * (index - lower);
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function sum(values) {
  return values.reduce((total, value) => total + (Number.isFinite(value) ? value : 0), 0);
}

function average(values) {
  const nums = values.filter((value) => Number.isFinite(value));
  return nums.length ? sum(nums) / nums.length : null;
}

function standardDeviation(values) {
  const nums = values.filter((value) => Number.isFinite(value));
  if (nums.length < 2) {
    return null;
  }
  const mean = average(nums);
  const variance = average(nums.map((value) => (value - mean) ** 2));
  return Math.sqrt(variance);
}

function rollingAverage(series, index, field, days) {
  if (index + 1 < days) {
    return null;
  }
  return average(series.slice(index - days + 1, index + 1).map((item) => item[field]));
}

function rollingHigh(series, index, days) {
  if (index + 1 < days) {
    return null;
  }
  return Math.max(...series.slice(index - days + 1, index + 1).map((item) => item.close));
}

function rollingLow(series, index, days) {
  if (index + 1 < days) {
    return null;
  }
  return Math.min(...series.slice(index - days + 1, index + 1).map((item) => item.close));
}

function rollingReturn(series, index, days) {
  if (index < days) {
    return null;
  }
  const base = series[index - days]?.close;
  const close = series[index]?.close;
  return base > 0 ? close / base - 1 : null;
}

function rollingVolatility(series, index, days) {
  if (index + 1 < days) {
    return null;
  }
  return standardDeviation(series.slice(index - days + 1, index + 1).map((item) => item.dailyRet));
}

function trendEfficiency(series, index, days) {
  if (index < days) {
    return null;
  }
  const totalReturn = rollingReturn(series, index, days);
  const path = sum(series.slice(index - days + 1, index + 1).map((item) => Math.abs(item.dailyRet)));
  return path > 0 ? totalReturn / path : null;
}

function parseIndexSeries(csvText) {
  if (!csvText) {
    return [];
  }

  const rows = parseCsv(csvText)
    .map((row) => ({
      tradeDate: normalizeDate(row.trade_date || row.date),
      open: toNumber(row.open),
      high: toNumber(row.high),
      low: toNumber(row.low),
      close: toNumber(row.close),
      amount: toNumber(row.amount) || 0,
    }))
    .filter((row) => row.tradeDate && row.open > 0 && row.high > 0 && row.low > 0 && row.close > 0)
    .sort((left, right) => left.tradeDate.localeCompare(right.tradeDate));

  return enrichSeries(rows.map((row, index) => ({
    ...row,
    dailyRet: index > 0 && rows[index - 1].close > 0 ? row.close / rows[index - 1].close - 1 : 0,
  })));
}

function enrichSeries(series) {
  return series.map((item, index) => {
    const ma20 = rollingAverage(series, index, 'close', 20);
    const ma60 = rollingAverage(series, index, 'close', 60);
    const high20 = rollingHigh(series, index, 20);
    const high60 = rollingHigh(series, index, 60);
    const low20 = rollingLow(series, index, 20);
    const vol10 = rollingVolatility(series, index, 10);
    const vol20 = rollingVolatility(series, index, 20);
    const amountMa5 = rollingAverage(series, index, 'amount', 5);
    const amountMa20 = rollingAverage(series, index, 'amount', 20);

    return {
      ...item,
      ma20,
      ma60,
      high20,
      high60,
      low20,
      ret3: rollingReturn(series, index, 3),
      ret5: rollingReturn(series, index, 5),
      ret10: rollingReturn(series, index, 10),
      ret20: rollingReturn(series, index, 20),
      vol10,
      vol20,
      amountRatio5: amountMa5 && amountMa20 ? amountMa5 / amountMa20 - 1 : null,
      drawdown20: high20 ? item.close / high20 - 1 : null,
      distanceToHigh20: high20 ? item.close / high20 - 1 : null,
      ma20Bias: ma20 ? item.close / ma20 - 1 : null,
      ma60Bias: ma60 ? item.close / ma60 - 1 : null,
      trendEfficiency20: trendEfficiency(series, index, 20),
      amplitude20: high20 && low20 ? high20 / low20 - 1 : null,
    };
  });
}

function featureVector(point) {
  return {
    ret3: point.ret3,
    ret5: point.ret5,
    ret10: point.ret10,
    ret20: point.ret20,
    vol10: point.vol10,
    vol20: point.vol20,
    amountRatio5: point.amountRatio5,
    drawdown20: point.drawdown20,
    ma20Bias: point.ma20Bias,
    ma60Bias: point.ma60Bias,
    trendEfficiency20: point.trendEfficiency20,
    amplitude20: point.amplitude20,
  };
}

const FEATURE_SCALES = {
  ret3: 0.04,
  ret5: 0.06,
  ret10: 0.09,
  ret20: 0.14,
  vol10: 0.025,
  vol20: 0.03,
  amountRatio5: 0.45,
  drawdown20: 0.08,
  ma20Bias: 0.08,
  ma60Bias: 0.12,
  trendEfficiency20: 0.45,
  amplitude20: 0.12,
};

const FEATURE_WEIGHTS = {
  ret3: 0.8,
  ret5: 1.0,
  ret10: 1.0,
  ret20: 0.8,
  vol10: 1.0,
  vol20: 0.8,
  amountRatio5: 0.8,
  drawdown20: 1.0,
  ma20Bias: 0.9,
  ma60Bias: 0.7,
  trendEfficiency20: 0.7,
  amplitude20: 0.6,
};

function featureDistance(left, right) {
  let total = 0;
  let weights = 0;

  Object.keys(FEATURE_SCALES).forEach((key) => {
    if (!Number.isFinite(left[key]) || !Number.isFinite(right[key])) {
      return;
    }
    const scale = FEATURE_SCALES[key];
    const weight = FEATURE_WEIGHTS[key];
    const normalized = (left[key] - right[key]) / scale;
    total += weight * normalized * normalized;
    weights += weight;
  });

  return weights > 0 ? Math.sqrt(total / weights) : Number.POSITIVE_INFINITY;
}

function forwardWorstDrawdown(series, index, horizon) {
  const currentClose = series[index]?.close;
  if (!currentClose || index + horizon >= series.length) {
    return null;
  }
  const lows = series.slice(index + 1, index + horizon + 1).map((item) => item.low);
  return Math.min(...lows) / currentClose - 1;
}

function forwardReturn(series, index, horizon) {
  const currentClose = series[index]?.close;
  const futureClose = series[index + horizon]?.close;
  return currentClose > 0 && futureClose > 0 ? futureClose / currentClose - 1 : null;
}

function calculateDrawdownModel(series) {
  const latestIndex = series.length - 1;
  const current = series[latestIndex];
  const currentFeatures = featureVector(current);
  const candidateEndIndex = latestIndex - Math.max(...DRAWDOWN_HORIZONS, ...UPSIDE_HORIZONS);

  const candidates = [];
  for (let index = 80; index <= candidateEndIndex; index += 1) {
    const point = series[index];
    const distance = featureDistance(currentFeatures, featureVector(point));
    if (!Number.isFinite(distance)) {
      continue;
    }
    candidates.push({ index, point, distance });
  }

  const sorted = candidates.sort((left, right) => left.distance - right.distance);
  const analogCount = clamp(Math.round(sorted.length * 0.24), MIN_ANALOG_COUNT, MAX_ANALOG_COUNT);
  const analogs = sorted.slice(0, analogCount);
  const distanceBase = median(analogs.map((item) => item.distance)) || 1;

  const probabilities = DRAWDOWN_HORIZONS.map((horizon) => {
    const threshold = -clamp((current.vol20 || 0.035) * Math.sqrt(horizon) * 0.85, 0.025, 0.1);
    const allDrawdowns = candidates
      .map((item) => forwardWorstDrawdown(series, item.index, horizon))
      .filter((value) => Number.isFinite(value));
    const analogDrawdowns = analogs
      .map((item) => ({
        value: forwardWorstDrawdown(series, item.index, horizon),
        weight: Math.exp(-item.distance / distanceBase),
      }))
      .filter((item) => Number.isFinite(item.value));

    const analogWeight = sum(analogDrawdowns.map((item) => item.weight));
    const analogProbability = analogWeight
      ? sum(analogDrawdowns.map((item) => (item.value <= threshold ? item.weight : 0))) / analogWeight
      : 0;
    const baselineProbability = allDrawdowns.length
      ? allDrawdowns.filter((value) => value <= threshold).length / allDrawdowns.length
      : 0;
    const probability = clamp(analogProbability * 0.72 + baselineProbability * 0.28, 0, 1);

    return {
      horizon,
      threshold,
      probability,
      analogProbability,
      baselineProbability,
      medianWorstDrawdown: median(analogDrawdowns.map((item) => item.value)),
      p75WorstDrawdown: percentile(analogDrawdowns.map((item) => item.value), 0.25),
      sampleCount: analogDrawdowns.length,
    };
  });

  const upsideProbabilities = UPSIDE_HORIZONS.map((horizon) => {
    const allReturns = candidates
      .map((item) => forwardReturn(series, item.index, horizon))
      .filter((value) => Number.isFinite(value));
    const analogReturns = analogs
      .map((item) => ({
        value: forwardReturn(series, item.index, horizon),
        weight: Math.exp(-item.distance / distanceBase),
      }))
      .filter((item) => Number.isFinite(item.value));

    const analogWeight = sum(analogReturns.map((item) => item.weight));
    const analogProbability = analogWeight
      ? sum(analogReturns.map((item) => (item.value > 0 ? item.weight : 0))) / analogWeight
      : 0;
    const baselineProbability = allReturns.length
      ? allReturns.filter((value) => value > 0).length / allReturns.length
      : 0;
    const probability = clamp(analogProbability * 0.72 + baselineProbability * 0.28, 0, 1);

    return {
      horizon,
      threshold: 0,
      probability,
      analogProbability,
      baselineProbability,
      medianForwardReturn: median(analogReturns.map((item) => item.value)),
      p25ForwardReturn: percentile(analogReturns.map((item) => item.value), 0.25),
      p75ForwardReturn: percentile(analogReturns.map((item) => item.value), 0.75),
      sampleCount: analogReturns.length,
    };
  });

  return {
    probabilities,
    upsideProbabilities,
    analogs: analogs.slice(0, 8).map((item) => ({
      tradeDate: item.point.tradeDate,
      distance: item.distance,
      ret10: item.point.ret10,
      drawdown20: item.point.drawdown20,
      amountRatio5: item.point.amountRatio5,
      vol20: item.point.vol20,
    })),
    totalCandidateCount: candidates.length,
  };
}

function findTrendStartIndex(series) {
  const latestIndex = series.length - 1;
  const start = Math.max(0, latestIndex - 80);
  const localLows = [];

  for (let index = start + 3; index <= latestIndex - 5; index += 1) {
    const window = series.slice(index - 3, index + 4);
    const low = Math.min(...window.map((item) => item.close));
    if (series[index].close <= low * 1.004) {
      localLows.push(index);
    }
  }

  const currentClose = series[latestIndex].close;
  const qualified = localLows
    .filter((index) => currentClose / series[index].close - 1 >= 0.06)
    .sort((left, right) => right - left);

  if (qualified.length > 0) {
    return qualified[0];
  }

  let minIndex = Math.max(0, latestIndex - 40);
  for (let index = minIndex + 1; index <= latestIndex; index += 1) {
    if (series[index].close < series[minIndex].close) {
      minIndex = index;
    }
  }
  return minIndex;
}

function findLocalHighs(series, startIndex, endIndex) {
  const highs = [];
  for (let index = Math.max(3, startIndex); index <= endIndex - 3; index += 1) {
    const window = series.slice(index - 3, index + 4);
    const high = Math.max(...window.map((item) => item.high));
    if (series[index].high >= high * 0.996) {
      highs.push({
        date: series[index].tradeDate,
        level: series[index].high,
        kind: '历史高点',
        weight: 1.2 + (index - startIndex) / Math.max(1, endIndex - startIndex),
      });
    }
  }
  return highs;
}

function clusterPressureLevels(rawLevels, currentClose) {
  const sorted = rawLevels
    .filter((item) => item.level > currentClose * 1.003)
    .sort((left, right) => left.level - right.level);

  const clusters = [];
  sorted.forEach((item) => {
    const cluster = clusters.find((entry) => Math.abs(entry.level - item.level) / entry.level <= 0.012);
    if (!cluster) {
      clusters.push({
        level: item.level,
        score: item.weight || 1,
        labels: new Set([item.kind]),
        dates: item.date ? [item.date] : [],
        count: 1,
      });
      return;
    }

    const nextScore = cluster.score + (item.weight || 1);
    cluster.level = (cluster.level * cluster.score + item.level * (item.weight || 1)) / nextScore;
    cluster.score = nextScore;
    cluster.labels.add(item.kind);
    if (item.date) {
      cluster.dates.push(item.date);
    }
    cluster.count += 1;
  });

  return clusters
    .map((cluster) => ({
      level: cluster.level,
      distance: cluster.level / currentClose - 1,
      reason: [...cluster.labels].join(' + '),
      touches: cluster.count,
      latestDate: cluster.dates.sort().at(-1) || '',
      score: cluster.score,
    }))
    .sort((left, right) => left.level - right.level)
    .slice(0, 7);
}

function calculatePressureLevels(series) {
  const latestIndex = series.length - 1;
  const current = series[latestIndex];
  const trendStartIndex = findTrendStartIndex(series);
  const trendLow = series[trendStartIndex];
  const lookbackStart = Math.max(0, latestIndex - 160);
  const rawLevels = [
    ...findLocalHighs(series, lookbackStart, latestIndex),
  ];

  [
    { days: 20, label: '20日高点', weight: 1.3 },
    { days: 60, label: '60日高点', weight: 1.5 },
    { days: 120, label: '120日高点', weight: 1.6 },
  ].forEach((item) => {
    const level = rollingHigh(series, latestIndex, item.days);
    if (Number.isFinite(level)) {
      rawLevels.push({ level, kind: item.label, weight: item.weight });
    }
  });

  const trendRange = current.close - trendLow.close;
  if (trendRange > 0) {
    [
      { ratio: 1.272, label: '趋势扩展1.272', weight: 1.0 },
      { ratio: 1.414, label: '趋势扩展1.414', weight: 0.9 },
      { ratio: 1.618, label: '趋势扩展1.618', weight: 0.8 },
      { ratio: 2.0, label: '趋势扩展2.000', weight: 0.7 },
    ].forEach((item) => {
      rawLevels.push({
        level: trendLow.close + trendRange * item.ratio,
        kind: item.label,
        weight: item.weight,
      });
    });
  }

  return {
    trendStart: {
      tradeDate: trendLow.tradeDate,
      level: trendLow.close,
      gain: current.close / trendLow.close - 1,
      days: latestIndex - trendStartIndex,
    },
    levels: clusterPressureLevels(rawLevels, current.close),
  };
}

function latestSummary(series, index) {
  const latest = series.at(-1);
  return {
    indexCode: index.indexCode,
    indexName: index.indexName,
    tradeDate: latest.tradeDate,
    close: latest.close,
    dailyRet: latest.dailyRet,
    ret5: latest.ret5,
    ret10: latest.ret10,
    ret20: latest.ret20,
    drawdown20: latest.drawdown20,
    vol20: latest.vol20,
    amountRatio5: latest.amountRatio5,
    ma20Bias: latest.ma20Bias,
    ma60Bias: latest.ma60Bias,
    dataPointCount: series.length,
  };
}

export function calculateIndexMarket(csvText, index = {}) {
  const indexConfig = {
    indexCode: index.indexCode || INDEX_CODE,
    indexName: index.indexName || INDEX_NAME,
  };
  const series = parseIndexSeries(csvText);

  if (series.length < 120) {
    throw new Error(`${indexConfig.indexName}指数历史K线样本不足，无法计算市场模型。`);
  }

  const model = calculateDrawdownModel(series);
  const pressure = calculatePressureLevels(series);

  return {
    summary: latestSummary(series, indexConfig),
    model,
    pressure,
    chart: series.slice(-80).map((item) => ({
      tradeDate: item.tradeDate,
      close: item.close,
      ma20: item.ma20,
    })),
  };
}

export function calculateKechuangMarket(csvText) {
  return calculateIndexMarket(csvText, { indexCode: INDEX_CODE, indexName: INDEX_NAME });
}
