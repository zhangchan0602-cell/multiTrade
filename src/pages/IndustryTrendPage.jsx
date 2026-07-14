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

function formatScore(value) {
  return Number.isFinite(value) ? `${value >= 0 ? '+' : ''}${value.toFixed(2)}` : '-';
}

function modelClass(score) {
  if (score >= 0.3) return 'model-positive';
  if (score <= -0.3) return 'model-negative';
  return 'model-neutral';
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

function ModelCell({ model, type }) {
  const detail = (() => {
    if (type === 'macd') {
      return `柱 ${formatPct(model?.histPct)} | 量比 ${Number.isFinite(model?.amountRatio) ? model.amountRatio.toFixed(2) : '-'}`;
    }
    if (type === 'quantile') {
      return `5日 ${formatPct(model?.ret5Quantile)} | 20日 ${formatPct(model?.ret20Quantile)}`;
    }
    return `MA差 ${formatPct(model?.maSpread)} | 斜率 ${formatPct(model?.ma20Slope5)}`;
  })();

  return (
    <div className={`industry-model industry-model-${type} ${modelClass(model?.score)}`}>
      <strong>{model?.signal || '-'}</strong>
      <span>分数 {formatScore(model?.score)}</span>
      <small>{detail}</small>
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
    <section className="content-stack industry-page">
      <article className="panel panel-intro industry-intro">
        <div className="industry-heading">
          <h2>行业趋势排行榜</h2>
          <p>以 MACD+量能共振、分位数极值、双均线三种模型形成行业趋势共识，并展示未来 3 / 5 / 10 个交易日的方向与回撤估计。</p>
          <p className="panel-meta-line">“事件”仅代表量价模型状态，不包含新闻、公告或政策事件。</p>
        </div>
        <div className="industry-actions">
          <button type="button" className="action-button action-primary" onClick={() => load({ refresh: true })} disabled={refreshing}>
            {refreshing ? '更新中...' : '更新数据'}
          </button>
        </div>
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
            <div className="industry-overview-head">
              <h3>当前行业状态</h3>
              <p className="panel-meta-line">
                行情日期 {formatDate(result.tradeDate)}，共 {overview.count} 个行业。
                {result.updatedAt ? ` 最近计算 ${new Date(result.updatedAt).toLocaleString('zh-CN', { hour12: false })}。` : ''}
              </p>
            </div>
            <div className="industry-stat-grid">
              <div className="industry-stat">
                <div className="meta-key">热度最高</div>
                <div className="meta-value">{overview.hottest?.industry || '-'}</div>
              </div>
              <div className="industry-stat">
                <div className="meta-key">最高热度</div>
                <div className="meta-value">{overview.hottest?.heat?.toFixed(1) || '-'}</div>
              </div>
              <div className="industry-stat">
                <div className="meta-key">高热行业</div>
                <div className="meta-value">{overview.hotCount} 个</div>
              </div>
              <div className="industry-stat">
                <div className="meta-key">数据来源</div>
                <div className="meta-value meta-value-small">本地日线</div>
              </div>
            </div>
            <div className="industry-model-weights" aria-label="模型权重">
              <span><strong>40%</strong> MACD + 量能</span>
              <span><strong>30%</strong> 分位数极值</span>
              <span><strong>30%</strong> 双均线</span>
            </div>
          </article>

          <article className="panel industry-ranking-panel">
            <div className="ops-card-head">
              <div>
                <h3>行业排名</h3>
                <p className="panel-meta-line">热度由 MACD+量能 40%、分位数极值 30%、双均线 30% 合成；“回”表示该期限内出现显著回撤的模型估计。</p>
              </div>
              <span className="status-chip status-success">
                {result.modelVersion >= 2 ? '三模型共识' : result.source || '行业模型'}
              </span>
            </div>
            <div className="table-wrap industry-table-wrap">
              <table className="industry-table">
                <colgroup>
                  <col className="industry-col-rank" />
                  <col className="industry-col-name" />
                  <col className="industry-col-data" />
                  <col className="industry-col-heat" />
                  <col className="industry-col-consensus" />
                  <col className="industry-col-model" />
                  <col className="industry-col-model" />
                  <col className="industry-col-model" />
                  <col className="industry-col-events" />
                  <col className="industry-col-probability" />
                  <col className="industry-col-probability" />
                  <col className="industry-col-probability" />
                </colgroup>
                <thead>
                  <tr>
                    <th>排名</th>
                    <th>行业</th>
                    <th>行业数据</th>
                    <th>热度</th>
                    <th>模型共识</th>
                    <th>MACD+量能</th>
                    <th>分位数极值</th>
                    <th>双均线</th>
                    <th>模型事件</th>
                    <th>3日 概率</th>
                    <th>5日 概率</th>
                    <th>10日 概率</th>
                  </tr>
                </thead>
                <tbody>
                  {result.industries.map((item) => (
                    <tr key={item.industry}>
                      <td className="industry-rank">{item.rank}</td>
                      <td className="industry-name"><strong>{item.industry}</strong></td>
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
                      <td>
                        <span className={`industry-consensus ${modelClass(item.consensusScore)}`}>
                          {formatScore(item.consensusScore)}
                        </span>
                      </td>
                      <td><ModelCell model={item.models?.macdVolume} type="macd" /></td>
                      <td><ModelCell model={item.models?.quantileExtreme} type="quantile" /></td>
                      <td><ModelCell model={item.models?.dualMA} type="dual" /></td>
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
