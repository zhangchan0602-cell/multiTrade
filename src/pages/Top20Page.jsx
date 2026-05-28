import React, { useEffect, useMemo, useState } from 'react';
import { fetchCombinedBoard, generateCombinedBoard } from '../lib/opsApi';
import { buildCombinedSnapshotFromPayload, getFactorDefinition, loadFactorSnapshots } from '../lib/top20';

const factorOptions = [
  { key: 'short', label: '盘后版Top10' },
  { key: 'tail', label: '尾盘版Top10' },
  { key: 'leader', label: '龙头抱团Top20' },
  { key: 'rps90', label: 'RPS双90 Top20' },
  { key: 'combined', label: '综合榜单' },
];

function formatNumber(value, digits = 2) {
  return Number.isFinite(value) ? value.toFixed(digits) : '-';
}

function makeRowKey(item) {
  return `${item.rank || ''}-${item.code || ''}`;
}

export default function Top20Page() {
  const [factorKey, setFactorKey] = useState('short');
  const [snapshots, setSnapshots] = useState([]);
  const [selectedDate, setSelectedDate] = useState('');
  const [loading, setLoading] = useState(true);
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
        let nextCombinedStatus = '';

        if (factorKey === 'combined') {
          try {
            const payload = await fetchCombinedBoard();
            const generatedSnapshot = buildCombinedSnapshotFromPayload(payload);
            if (generatedSnapshot) {
              loadedSnapshots = [generatedSnapshot];
              nextCombinedStatus = '当前展示的是已生成并落盘的综合榜。';
            }
          } catch (apiError) {
            nextCombinedStatus = '本地操作服务不可用，当前展示页面实时交集预览。';
          }
        }

        if (loadedSnapshots.length === 0) {
          loadedSnapshots = await loadFactorSnapshots(factorKey);
          if (factorKey === 'combined' && !nextCombinedStatus) {
            nextCombinedStatus = '当前展示页面实时交集预览，点击“生成综合榜”可写出当日综合榜文件。';
          }
        }

        if (!mounted) {
          return;
        }
        setSnapshots(loadedSnapshots);
        setSelectedDate(loadedSnapshots.slice(-1)[0]?.date || '');
        setCombinedStatus(factorKey === 'combined' ? nextCombinedStatus : '');
        setLoading(false);
      } catch (err) {
        if (!mounted) {
          return;
        }
        setError(err.message || '加载榜单失败');
        setSnapshots([]);
        setSelectedDate('');
        setCombinedStatus('');
        setLoading(false);
      }
    }

    loadSnapshots();

    return () => {
      mounted = false;
    };
  }, [factorKey]);

  const currentSnapshot = useMemo(
    () => snapshots.find((snapshot) => snapshot.date === selectedDate) || snapshots.slice(-1)[0] || null,
    [selectedDate, snapshots]
  );

  const previousSnapshot = useMemo(() => {
    if (!currentSnapshot) {
      return null;
    }
    const currentIndex = snapshots.findIndex((snapshot) => snapshot.date === currentSnapshot.date);
    return currentIndex > 0 ? snapshots[currentIndex - 1] : null;
  }, [currentSnapshot, snapshots]);

  const previousCodes = useMemo(
    () => new Set((previousSnapshot?.items || []).map((item) => item.code)),
    [previousSnapshot]
  );

  // 计算当前榜单每只股票的连续上榜次数
  const streakMap = useMemo(() => {
    if (!currentSnapshot || snapshots.length <= 1) return new Map();
    const currentIndex = snapshots.findIndex((snapshot) => snapshot.date === currentSnapshot.date);
    const result = new Map();
    for (const item of currentSnapshot.items) {
      let streak = 1;
      for (let i = currentIndex - 1; i >= 0; i--) {
        if (snapshots[i].items.some((s) => s.code === item.code)) {
          streak++;
        } else {
          break;
        }
      }
      result.set(item.code, streak);
    }
    return result;
  }, [currentSnapshot, snapshots]);

  const removedItems = useMemo(() => {
    if (!previousSnapshot || !currentSnapshot) {
      return [];
    }
    const currentCodes = new Set(currentSnapshot.items.map((item) => item.code));
    return previousSnapshot.items.filter((item) => !currentCodes.has(item.code)).slice(0, 8);
  }, [currentSnapshot, previousSnapshot]);

  const isRps = factorKey === 'rps90';
  const isCombined = factorKey === 'combined';
  const quoteOnly = currentSnapshot?.items?.some((item) => item.quoteOnlyFallbackUsed) || false;

  async function handleGenerateCombined() {
    setCombinedGenerating(true);
    setActionError('');

    try {
      const payload = await generateCombinedBoard();
      const generatedSnapshot = buildCombinedSnapshotFromPayload(payload);
      setCombinedStatus('当前展示的是已生成并落盘的综合榜。');
      setSnapshots(generatedSnapshot ? [generatedSnapshot] : []);
      setSelectedDate(generatedSnapshot?.date || '');
    } catch (err) {
      setActionError(err.message || '生成综合榜失败');
    } finally {
      setCombinedGenerating(false);
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
          {isCombined && <p className="panel-meta-line">入榜规则：当天同时进入短线盘后版 Top10、RPS双90 Top20、龙头抱团 Top20 中的任意两个榜单。</p>}
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
          {currentSnapshot && snapshots.length > 1 && (
            <select value={currentSnapshot.date} onChange={(event) => setSelectedDate(event.target.value)}>
              {snapshots.map((snapshot) => (
                <option key={snapshot.date} value={snapshot.date}>
                  {snapshot.date}
                </option>
              ))}
            </select>
          )}
        </div>
      </article>

      {actionError && <article className="panel error">{actionError}</article>}

      {!currentSnapshot && <article className="panel">{definition.emptyMessage}</article>}

      {currentSnapshot && quoteOnly && (
        <article className="freshness-banner freshness-warn">
          <strong>历史K线不可用</strong>
          <p>当前{definition.title}为纯行情降级候选，适合先观察，实盘优先等待真实K线结果。</p>
        </article>
      )}

      {currentSnapshot && (
        <>
          <article className="freshness-banner freshness-warn">
            <strong>当前仅提供单票层风控</strong>
            <p>后端当前未加入指数环境过滤、市场风格切换、行业集中度约束、单日组合最大敞口和容量约束，榜单候选可能集中于同一题材或风格。</p>
          </article>

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
                    const streak = streakMap.get(item.code) || 1;
                    const stateLabel = item.quoteOnlyFallbackUsed
                      ? '降级'
                      : streak >= 2
                        ? `连续${streak}日`
                        : isNew
                          ? '新增'
                          : '跟踪';
                    const stateClass = item.quoteOnlyFallbackUsed
                      ? 'badge-flat'
                      : streak >= 2
                        ? 'badge-streak'
                        : isNew
                          ? 'badge-new'
                          : 'badge-up';

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