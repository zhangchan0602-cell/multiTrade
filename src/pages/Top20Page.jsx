import React, { useEffect, useMemo, useState } from 'react';
import { getFactorDefinition, loadFactorSnapshots } from '../lib/top20';

const factorOptions = [
  { key: 'short', label: '短线Top5' },
  { key: 'mid', label: '中线Top20' },
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

  const definition = getFactorDefinition(factorKey);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    setError('');

    loadFactorSnapshots(factorKey)
      .then((loadedSnapshots) => {
        if (!mounted) {
          return;
        }
        setSnapshots(loadedSnapshots);
        setSelectedDate(loadedSnapshots.slice(-1)[0]?.date || '');
        setLoading(false);
      })
      .catch((err) => {
        if (!mounted) {
          return;
        }
        setError(err.message || '加载榜单失败');
        setSnapshots([]);
        setSelectedDate('');
        setLoading(false);
      });

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

  const removedItems = useMemo(() => {
    if (!previousSnapshot || !currentSnapshot) {
      return [];
    }
    const currentCodes = new Set(currentSnapshot.items.map((item) => item.code));
    return previousSnapshot.items.filter((item) => !currentCodes.has(item.code)).slice(0, 8);
  }, [currentSnapshot, previousSnapshot]);

  const quoteOnly = currentSnapshot?.items?.some((item) => item.quoteOnlyFallbackUsed) || false;

  if (loading) {
    return <section className="panel">正在加载...</section>;
  }

  if (error) {
    return <section className="panel error">{error}</section>;
  }

  if (!currentSnapshot) {
    return <section className="panel">{definition.emptyMessage}</section>;
  }

  return (
    <section className="content-stack">
      <article className="panel panel-intro">
        <div>
          <h2>{definition.title}</h2>
          <p>{definition.subtitle}</p>
          <p className="panel-meta-line">{definition.description}</p>
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
          {snapshots.length > 1 && (
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

      {quoteOnly && (
        <article className="freshness-banner freshness-warn">
          <strong>历史K线不可用</strong>
          <p>当前短线Top5为纯行情降级候选，适合先观察，实盘优先等待真实K线结果。</p>
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
            </thead>
            <tbody>
              {currentSnapshot.items.map((item) => {
                const isNew = previousSnapshot && !previousCodes.has(item.code);
                const stateLabel = item.quoteOnlyFallbackUsed ? '降级' : isNew ? '新增' : '跟踪';
                const stateClass = item.quoteOnlyFallbackUsed ? 'badge-flat' : isNew ? 'badge-new' : 'badge-up';

                return (
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
    </section>
  );
}