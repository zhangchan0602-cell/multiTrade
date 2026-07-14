import React, { useEffect, useMemo, useState } from 'react';
import { fetchCombinedBoard, generateCombinedBoard } from '../lib/opsApi';
import {
  buildCombinedSnapshotFromPayload,
  getFactorDefinition,
  loadFactorHistorySnapshot,
  loadFactorSnapshots,
} from '../lib/top20';

const factorOptions = [
  { key: 'short', label: '盘后概率Top10' },
  { key: 'tail', label: '收盘资金Top10' },
  { key: 'leader', label: '龙头抱团Top20' },
  { key: 'rps90', label: 'RPS双90 Top20' },
  { key: 'combined', label: '综合榜单' },
];

function formatNumber(value, digits = 2) {
  return Number.isFinite(value) ? value.toFixed(digits) : '-';
}

function formatPctValue(value, digits = 2) {
  return Number.isFinite(value) ? `${(value * 100).toFixed(digits)}%` : '-';
}

function makeRowKey(item) {
  return `${item.rank || ''}-${item.code || ''}`;
}

function mergeSnapshots(current, additions) {
  const byId = new Map(current.map((snapshot) => [snapshot.id, snapshot]));
  additions.filter(Boolean).forEach((snapshot) => byId.set(snapshot.id, snapshot));
  return Array.from(byId.values()).sort((left, right) => String(left.date).localeCompare(String(right.date)));
}

export default function Top20Page() {
  const [factorKey, setFactorKey] = useState('short');
  const [snapshots, setSnapshots] = useState([]);
  const [historyEntries, setHistoryEntries] = useState([]);
  const [selectedSnapshotId, setSelectedSnapshotId] = useState('');
  const [loading, setLoading] = useState(true);
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  const [combinedGenerating, setCombinedGenerating] = useState(false);
  const [combinedStatus, setCombinedStatus] = useState('');

  const definition = getFactorDefinition(factorKey);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    setError('');
    setActionError('');

    async function loadSnapshots() {
      try {
        let loadedSnapshots = [];
        let nextHistoryEntries = [];
        let nextCombinedStatus = '';

        if (factorKey === 'combined') {
          try {
            const payload = await fetchCombinedBoard();
            const generatedSnapshot = buildCombinedSnapshotFromPayload(payload);
            if (generatedSnapshot) {
              loadedSnapshots = [{ ...generatedSnapshot, id: 'combined:current', isCurrent: true }];
              nextCombinedStatus = '当前展示的是已生成并落盘的综合榜。';
            }
          } catch (apiError) {
            nextCombinedStatus = '本地操作服务不可用，当前展示页面实时交集预览。';
          }
        }

        if (loadedSnapshots.length === 0) {
          const loaded = await loadFactorSnapshots(factorKey);
          loadedSnapshots = loaded.snapshots;
          nextHistoryEntries = loaded.historyEntries;
          if (factorKey === 'combined' && !nextCombinedStatus) {
            nextCombinedStatus = '当前展示页面实时交集预览，点击“生成综合榜”可写出当日综合榜文件。';
          }
        }

        const initialSnapshot = loadedSnapshots.find((snapshot) => snapshot.isCurrent) || loadedSnapshots.at(-1) || null;
        const previousEntry = initialSnapshot
          ? nextHistoryEntries.filter((entry) => entry.date < initialSnapshot.date).at(-1)
          : null;
        if (previousEntry) {
          const previousSnapshot = await loadFactorHistorySnapshot(factorKey, previousEntry.id);
          loadedSnapshots = mergeSnapshots(loadedSnapshots, [previousSnapshot]);
        }

        if (!mounted) {
          return;
        }
        setSnapshots(loadedSnapshots);
        setHistoryEntries(nextHistoryEntries);
        setSelectedSnapshotId(initialSnapshot?.id || '');
        setCombinedStatus(factorKey === 'combined' ? nextCombinedStatus : '');
        setLoading(false);
      } catch (err) {
        if (!mounted) {
          return;
        }
        setError(err.message || '加载榜单失败');
        setSnapshots([]);
        setHistoryEntries([]);
        setSelectedSnapshotId('');
        setCombinedStatus('');
        setLoading(false);
      }
    }

    loadSnapshots();

    return () => {
      mounted = false;
    };
  }, [factorKey]);

  const snapshotOptions = useMemo(() => {
    const options = new Map();
    historyEntries.forEach((entry) => options.set(entry.id, entry));
    snapshots.forEach((snapshot) => {
      if (snapshot.isCurrent || !options.has(snapshot.id)) {
        options.set(snapshot.id, snapshot);
      }
    });
    return Array.from(options.values()).sort((left, right) => String(left.date).localeCompare(String(right.date)));
  }, [historyEntries, snapshots]);

  const currentSnapshot = useMemo(
    () => snapshots.find((snapshot) => snapshot.id === selectedSnapshotId) || null,
    [selectedSnapshotId, snapshots]
  );

  const previousSnapshot = useMemo(() => {
    if (!currentSnapshot) {
      return null;
    }
    const currentIndex = snapshotOptions.findIndex((snapshot) => snapshot.id === currentSnapshot.id);
    const previousId = currentIndex > 0 ? snapshotOptions[currentIndex - 1].id : null;
    return snapshots.find((snapshot) => snapshot.id === previousId) || null;
  }, [currentSnapshot, snapshotOptions, snapshots]);

  const previousCodes = useMemo(
    () => new Set((previousSnapshot?.items || []).map((item) => item.code)),
    [previousSnapshot]
  );

  const removedItems = useMemo(() => {
    if (!previousSnapshot || !currentSnapshot) {
      return [];
    }
    const currentCodes = new Set(currentSnapshot.items.map((item) => item.code));
    return previousSnapshot.items.filter((item) => !currentCodes.has(item.code)).slice(0, 8);
  }, [currentSnapshot, previousSnapshot]);

  const isRps = factorKey === 'rps90';
  const isCombined = factorKey === 'combined';
  const isPostclose = factorKey === 'short';
  const isProbabilityModel = factorKey === 'short' || factorKey === 'tail';
  const quoteOnly = currentSnapshot?.items?.some((item) => item.quoteOnlyFallbackUsed) || false;
  const marketGateFailed = isPostclose && currentSnapshot?.items?.some((item) => item.passMarketEnv === false);

  async function handleGenerateCombined() {
    setCombinedGenerating(true);
    setActionError('');

    try {
      const payload = await generateCombinedBoard();
      const generatedSnapshot = buildCombinedSnapshotFromPayload(payload);
      setCombinedStatus('当前展示的是已生成并落盘的综合榜。');
      setSnapshots(generatedSnapshot ? [{ ...generatedSnapshot, id: 'combined:current', isCurrent: true }] : []);
      setSelectedSnapshotId(generatedSnapshot ? 'combined:current' : '');
    } catch (err) {
      setActionError(err.message || '生成综合榜失败');
    } finally {
      setCombinedGenerating(false);
    }
  }

  async function handleSnapshotChange(id) {
    setActionError('');
    setSelectedSnapshotId(id);
    const entry = historyEntries.find((item) => item.id === id);
    if (!entry || snapshots.some((snapshot) => snapshot.id === id)) {
      return;
    }

    setSnapshotLoading(true);
    try {
      const entryIndex = historyEntries.findIndex((item) => item.id === id);
      const previousEntry = entryIndex > 0 ? historyEntries[entryIndex - 1] : null;
      const loadedIds = new Set(snapshots.map((snapshot) => snapshot.id));
      const entriesToLoad = [entry, previousEntry].filter((item) => item && !loadedIds.has(item.id));
      const loadedSnapshots = await Promise.all(entriesToLoad.map((item) => loadFactorHistorySnapshot(factorKey, item.id)));
      setSnapshots((current) => mergeSnapshots(current, loadedSnapshots));
    } catch (err) {
      setActionError(err.message || '加载历史榜单失败');
    } finally {
      setSnapshotLoading(false);
    }
  }

  if (loading) {
    return <section className="panel">正在加载...</section>;
  }

  if (error) {
    return <section className="panel error">{error}</section>;
  }

  return (
    <section className="content-stack">
      <article className="panel panel-intro">
        <div>
          <h2>{definition.title}</h2>
          <p>{definition.subtitle}</p>
          <p className="panel-meta-line">{definition.description}</p>
          <p className="panel-meta-line">{definition.riskNote}</p>
          {isCombined && <p className="panel-meta-line">入榜规则：当天同时进入盘后三日上涨概率 Top10、RPS双90 Top20、龙头抱团 Top20 中的任意两个榜单。</p>}
          {isCombined && combinedStatus && <p className="panel-meta-line">{combinedStatus}</p>}
        </div>
        <div className="history-tools">
          {factorOptions.map((option) => (
            <button
              key={option.key}
              type="button"
              className={option.key === factorKey ? 'nav-link nav-link-active' : 'nav-link'}
              onClick={() => setFactorKey(option.key)}
            >
              {option.label}
            </button>
          ))}
          {isCombined && (
            <button
              type="button"
              className="action-button action-primary"
              onClick={handleGenerateCombined}
              disabled={combinedGenerating}
            >
              {combinedGenerating ? '生成中...' : '生成综合榜'}
            </button>
          )}
          {currentSnapshot && snapshotOptions.length > 1 && (
            <select
              value={selectedSnapshotId}
              onChange={(event) => handleSnapshotChange(event.target.value)}
              disabled={snapshotLoading}
              aria-label="选择榜单日期"
            >
              {snapshotOptions.map((snapshot) => (
                <option key={snapshot.id} value={snapshot.id}>
                  {snapshot.date}{snapshot.isCurrent ? '（当前）' : ''}
                </option>
              ))}
            </select>
          )}
        </div>
      </article>

      {actionError && <article className="panel error">{actionError}</article>}

      {snapshotLoading && <article className="panel">正在加载所选历史榜单...</article>}

      {!currentSnapshot && <article className="panel">{definition.emptyMessage}</article>}

      {currentSnapshot && quoteOnly && (
        <article className="freshness-banner freshness-warn">
          <strong>历史K线不可用</strong>
          <p>当前{definition.title}为纯行情降级候选，适合先观察，实盘优先等待真实K线结果。</p>
        </article>
      )}

      {currentSnapshot && marketGateFailed && (
        <article className="freshness-banner freshness-danger">
          <strong>市场环境闸门未通过</strong>
          <p>当日盘后版的市场广度或弱势股比例不满足开仓条件；当前 Top10 仅用于观察，不应视为执行清单。</p>
        </article>
      )}

      {currentSnapshot && (
        <>
          {!isCombined && (
            <article className="freshness-banner freshness-warn">
              <strong>{isPostclose ? '榜单与执行清单口径不同' : '组合层约束仍需人工确认'}</strong>
              <p>
                {isPostclose
                  ? '此处展示的是评分前列候选。实际执行仍应以交易过滤、市场环境闸门和单行业上限后的最终清单为准。'
                  : '当前榜单以单票筛选或策略共识为主，未统一控制组合总敞口、容量和跨策略相关性。'}
              </p>
            </article>
          )}

          <article className="panel">
            <div className="stat-row">
              <div className="stat-card">
                <div className="stat-value">{currentSnapshot.items.length}</div>
                <div className="stat-label">当前数量</div>
              </div>
              <div className="stat-card">
                <div className="stat-value">{currentSnapshot.generatedAt || '-'}</div>
                <div className="stat-label">生成时间</div>
              </div>
              <div className="stat-card">
                <div className="stat-value">{currentSnapshot.latestTradeDate || '-'}</div>
                <div className="stat-label">行情日期</div>
              </div>
            </div>

            <div className="table-wrap">
              <table>
                <thead>
                  {isCombined ? (
                    <tr>
                      <th>排名</th>
                      <th>代码</th>
                      <th>名称</th>
                      <th>行业</th>
                      <th>综合分</th>
                      <th>盘后分</th>
                      <th>龙头分</th>
                      <th>RPS分</th>
                      <th>RPS20</th>
                      <th>RPS90</th>
                      <th>状态</th>
                    </tr>
                  ) : isRps ? (
                    <tr>
                      <th>排名</th>
                      <th>代码</th>
                      <th>名称</th>
                      <th>行业</th>
                      <th>综合分</th>
                      <th>RPS20</th>
                      <th>RPS90</th>
                      <th>20日涨幅%</th>
                      <th>90日涨幅%</th>
                      <th>收盘价</th>
                      <th>成交额(亿)</th>
                      <th>状态</th>
                    </tr>
                  ) : isProbabilityModel ? (
                    <tr>
                      <th>排名</th>
                      <th>代码</th>
                      <th>名称</th>
                      <th>行业</th>
                      <th>三日上涨概率</th>
                      <th>预期3日收益</th>
                      <th>置信度</th>
                      <th>启动</th>
                      <th>趋势</th>
                      <th>活跃</th>
                      <th>状态</th>
                    </tr>
                  ) : (
                    <tr>
                      <th>排名</th>
                      <th>代码</th>
                      <th>名称</th>
                      <th>行业</th>
                      <th>得分</th>
                      <th>启动</th>
                      <th>趋势</th>
                      <th>动量</th>
                      <th>活跃</th>
                      <th>稳定</th>
                      <th>流动</th>
                      <th>状态</th>
                    </tr>
                  )}
                </thead>
                <tbody>
                  {currentSnapshot.items.map((item) => {
                    const isNew = previousSnapshot && !previousCodes.has(item.code);
                    const stateLabel = item.quoteOnlyFallbackUsed
                      ? '降级'
                      : !previousSnapshot
                        ? '待比对'
                        : isNew
                          ? '新增'
                          : '持续入选';
                    const stateClass = item.quoteOnlyFallbackUsed
                      ? 'badge-flat'
                      : !previousSnapshot
                        ? 'badge-flat'
                        : isNew
                          ? 'badge-new'
                          : 'badge-streak';

                    return isCombined ? (
                      <tr key={makeRowKey(item)}>
                        <td>{item.rank}</td>
                        <td>{item.code}</td>
                        <td>{item.name}</td>
                        <td className="industry-col">{item.industry}</td>
                        <td>{formatNumber(item.score100)}</td>
                        <td>{formatNumber(item.shortScore100)}</td>
                        <td>{formatNumber(item.leaderScore100)}</td>
                        <td>{formatNumber(item.rps90Score100)}</td>
                        <td>{formatNumber(item.rps20)}</td>
                        <td>{formatNumber(item.rps90)}</td>
                        <td>
                          <span className={`badge ${stateClass}`}>{stateLabel}</span>
                        </td>
                      </tr>
                    ) : isRps ? (
                      <tr key={makeRowKey(item)}>
                        <td>{item.rank}</td>
                        <td>{item.code}</td>
                        <td>{item.name}</td>
                        <td className="industry-col">{item.industry}</td>
                        <td>{formatNumber(item.score100)}</td>
                        <td>{formatNumber(item.rps20)}</td>
                        <td>{formatNumber(item.rps90)}</td>
                        <td>{formatNumber(item.ret20dPct)}</td>
                        <td>{formatNumber(item.ret90dPct)}</td>
                        <td>{formatNumber(item.closePx)}</td>
                        <td>{item.amountToday != null ? (item.amountToday / 1e8).toFixed(2) : '-'}</td>
                        <td>
                          <span className={`badge ${stateClass}`}>{stateLabel}</span>
                        </td>
                      </tr>
                    ) : isProbabilityModel ? (
                      <tr key={makeRowKey(item)}>
                        <td>{item.rank}</td>
                        <td>{item.code}</td>
                        <td>{item.name}</td>
                        <td className="industry-col">{item.industry}</td>
                        <td>{formatPctValue(item.upProb3d)}</td>
                        <td>{formatPctValue(item.expectedRet3d)}</td>
                        <td>{formatPctValue(item.upProb3dConfidence)}</td>
                        <td>{formatNumber(item.launchScore)}</td>
                        <td>{formatNumber(item.trendScore)}</td>
                        <td>{formatNumber(item.activityScore)}</td>
                        <td>
                          <span className={`badge ${stateClass}`}>{stateLabel}</span>
                        </td>
                      </tr>
                    ) : (
                      <tr key={makeRowKey(item)}>
                        <td>{item.rank}</td>
                        <td>{item.code}</td>
                        <td>{item.name}</td>
                        <td className="industry-col">{item.industry}</td>
                        <td>{formatNumber(item.score100)}</td>
                        <td>{formatNumber(item.launchScore)}</td>
                        <td>{formatNumber(item.trendScore)}</td>
                        <td>{formatNumber(item.momentumScore)}</td>
                        <td>{formatNumber(item.activityScore)}</td>
                        <td>{formatNumber(item.stabilityScore)}</td>
                        <td>{formatNumber(item.liquidityScore)}</td>
                        <td>
                          <span className={`badge ${stateClass}`}>{stateLabel}</span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </article>

          {removedItems.length > 0 && (
            <article className="panel">
              <h3>本期移出</h3>
              <ul className="removed-list">
                {removedItems.map((item) => (
                  <li key={makeRowKey(item)}>
                    <strong>{item.code}</strong>
                    <span>{item.name}</span>
                    <em>#{item.rank}</em>
                  </li>
                ))}
              </ul>
            </article>
          )}
        </>
      )}
    </section>
  );
}
