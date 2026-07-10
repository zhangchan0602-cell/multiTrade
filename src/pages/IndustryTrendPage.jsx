import React, { useEffect, useMemo, useState } from 'react';
import { fetchIndustryTrendRank, refreshIndustryTrendRank } from '../lib/opsApi';

function formatPct(value) {
  return Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '-';
}

function formatDate(value) {
  const digits = String(value || '').replace(/\D/g, '');
  return digits.length === 8 ? `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6, 8)}` : '-';
}

function heatClass(heat) {
  if (heat >= 70) return 'heat-hot';
  if (heat >= 50) return 'heat-warm';
  return 'heat-cool';
}

function probabilityClass(up, drawdown) {
  if (up >= 0.62 && drawdown < 0.36) return 'prob-positive';
  if (drawdown >= 0.48) return 'prob-risk';
  return 'prob-neutral';
}

function ProbabilityCell({ probability }) {
  const up = probability?.up;
  const drawdown = probability?.drawdown;
  return (
    <div className={`prob-pair ${probabilityClass(up, drawdown)}`}>
      <span>涨 {formatPct(up)}</span>
      <span>回 {formatPct(drawdown)}</span>
    </div>
  );
}

export default function IndustryTrendPage() {
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  async function load({ refresh = false } = {}) {
    setError('');
    if (refresh) setRefreshing(true);
    else setLoading(true);
    try {
      const payload = refresh ? await refreshIndustryTrendRank() : await fetchIndustryTrendRank();
      if (!refresh && !payload.exists) {
        const generated = await refreshIndustryTrendRank();
        setResult(generated);
      } else {
        setResult(payload);
      }
    } catch (err) {
      setError(err.message || '行业趋势数据生成失败');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const overview = useMemo(() => {
    const industries = result?.industries || [];
    const hottest = industries[0];
    const hotCount = industries.filter((item) => item.heat >= 70).length;
    return { hottest, hotCount, count: industries.length };
  }, [result]);

  return (
    <section className="content-stack">
      <article className="panel panel-intro industry-intro">
        <div>
          <h2>行业趋势排行榜</h2>
          <p>按行业整体动能、上涨扩散和成交量变化排序，展示未来 3 / 5 / 10 个交易日的方向与回撤模型估计。</p>
          <p className="panel-meta-line">“事件”仅代表量价模型状态，不包含新闻、公告或政策事件。</p>
        </div>
        <button type="button" className="action-button action-primary" onClick={() => load({ refresh: true })} disabled={refreshing}>
          {refreshing ? '更新中...' : '更新数据'}
        </button>
      </article>

      {error && <article className="panel error">{error}</article>}

      {loading && !error && (
        <article className="panel industry-empty">
          <h3>正在生成行业截面</h3>
          <p>读取本地全市场日线缓存并计算行业热度、趋势事件和概率估计。</p>
        </article>
      )}

      {!loading && result?.exists && (
        <>
          <article className="panel industry-overview">
            <div>
              <h3>当前行业状态</h3>
              <p className="panel-meta-line">
                行情日期 {formatDate(result.tradeDate)}，共 {overview.count} 个行业。
                {result.updatedAt ? ` 最近计算 ${new Date(result.updatedAt).toLocaleString('zh-CN', { hour12: false })}。` : ''}
              </p>
            </div>
            <div className="industry-stat-grid">
              <div className="meta-card">
                <div className="meta-key">热度最高</div>
                <div className="meta-value">{overview.hottest?.industry || '-'}</div>
              </div>
              <div className="meta-card">
                <div className="meta-key">最高热度</div>
                <div className="meta-value">{overview.hottest?.heat?.toFixed(1) || '-'}</div>
              </div>
              <div className="meta-card">
                <div className="meta-key">高热行业</div>
                <div className="meta-value">{overview.hotCount} 个</div>
              </div>
              <div className="meta-card">
                <div className="meta-key">数据来源</div>
                <div className="meta-value meta-value-small">本地日线</div>
              </div>
            </div>
          </article>

          <article className="panel industry-ranking-panel">
            <div className="ops-card-head">
              <div>
                <h3>行业排名</h3>
                <p className="panel-meta-line">概率为当前趋势、扩散度和量能状态的模型估计；“回”表示该期限内出现显著回撤的概率。</p>
              </div>
              <span className="status-chip status-success">{result.source || '行业模型'}</span>
            </div>
            <div className="table-wrap industry-table-wrap">
              <table className="industry-table">
                <thead>
                  <tr>
                    <th>排名</th>
                    <th>行业</th>
                    <th>行业数据</th>
                    <th>热度</th>
                    <th>模型事件</th>
                    <th>3日 概率</th>
                    <th>5日 概率</th>
                    <th>10日 概率</th>
                  </tr>
                </thead>
                <tbody>
                  {result.industries.map((item) => (
                    <tr key={item.industry}>
                      <td>{item.rank}</td>
                      <td><strong>{item.industry}</strong></td>
                      <td className="industry-data-cell">
                        <span>{item.memberCount} 只成分股</span>
                        <small>3/5/10日: {formatPct(item.ret3)} / {formatPct(item.ret5)} / {formatPct(item.ret10)}</small>
                      </td>
                      <td>
                        <div className={`heat-meter ${heatClass(item.heat)}`}>
                          <span style={{ width: `${Math.max(0, Math.min(100, item.heat || 0))}%` }} />
                          <strong>{item.heat?.toFixed(1) || '-'}</strong>
                        </div>
                      </td>
                      <td className="industry-event-cell">
                        {item.events.map((event) => <span key={event} className="badge badge-flat">{event}</span>)}
                      </td>
                      <td><ProbabilityCell probability={item.probabilities?.['3']} /></td>
                      <td><ProbabilityCell probability={item.probabilities?.['5']} /></td>
                      <td><ProbabilityCell probability={item.probabilities?.['10']} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </article>
        </>
      )}
    </section>
  );
}
