import React, { useEffect, useMemo, useState } from 'react';
import { parseCsv } from '../lib/csv';
import { fetchOpsHealth, fetchOpsJobs, fetchOpsTop5, getOpsApiBase, runOpsJob } from '../lib/opsApi';

const JOB_PRESETS = {
  postclose: { key: 'postclose', title: '短线多因子-盘后版', hint: '运行盘后版筛选，并刷新当天 Top5 候选。' },
  tail: { key: 'tail', title: '短线多因子-尾盘版', hint: '运行尾盘版筛选，并刷新当天 Top5 候选。' },
  rps90: { key: 'rps90', title: '策略-RPS双90', hint: '运行 RPS 双90 筛选，并刷新当天 Top5 候选。' },
  leader: { key: 'leader', title: '策略-龙头抱团', hint: '运行龙头抱团模型筛选，歌加行业领先与抱团特征标的。' },
};

const JOB_PRIORITY = ['postclose', 'tail', 'rps90', 'leader'];

function normalizeJobMeta(job) {
  const preset = JOB_PRESETS[job.key] || {};
  return {
    ...job,
    title: preset.title || job.label || job.key,
    hint: preset.hint || `执行 ${preset.title || job.label || job.key}。`,
  };
}

function orderJobs(jobList) {
  return [...jobList]
    .map(normalizeJobMeta)
    .sort((left, right) => {
      const leftIndex = JOB_PRIORITY.indexOf(left.key);
      const rightIndex = JOB_PRIORITY.indexOf(right.key);
      const safeLeft = leftIndex === -1 ? Number.MAX_SAFE_INTEGER : leftIndex;
      const safeRight = rightIndex === -1 ? Number.MAX_SAFE_INTEGER : rightIndex;
      return safeLeft - safeRight || left.title.localeCompare(right.title, 'zh-CN');
    });
}

function extractMeta(markdown, label) {
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const matched = markdown?.match(new RegExp(`^- ${escaped}:\\s*(.+)$`, 'm'));
  return matched ? matched[1].trim() : '-';
}

function formatState(status) {
  switch (status) {
    case 'running':
      return '运行中';
    case 'success':
      return '已完成';
    case 'error':
      return '运行失败';
    default:
      return '待执行';
  }
}

function statusClass(status) {
  switch (status) {
    case 'running':
      return 'status-chip status-running';
    case 'success':
      return 'status-chip status-success';
    case 'error':
      return 'status-chip status-error';
    default:
      return 'status-chip status-idle';
  }
}

export default function OpsPage() {
  const [apiOk, setApiOk] = useState(true);
  const [apiMessage, setApiMessage] = useState('');
  const [jobs, setJobs] = useState({});
  const [top5Map, setTop5Map] = useState({});
  const [actionError, setActionError] = useState('');
  const [loading, setLoading] = useState(true);
  const [selectedJobKey, setSelectedJobKey] = useState('');

  const orderedJobs = useMemo(() => orderJobs(Object.values(jobs)), [jobs]);

  useEffect(() => {
    let mounted = true;

    async function refreshDashboard() {
      try {
        await fetchOpsHealth();
        if (!mounted) {
          return;
        }
        setApiOk(true);
        setApiMessage('');

        const { jobs: jobList } = await fetchOpsJobs();
        if (!mounted) {
          return;
        }

        const nextJobs = Object.fromEntries(jobList.map((item) => [item.key, item]));
        setJobs(nextJobs);
        setSelectedJobKey((current) => {
          if (current && nextJobs[current]) {
            return current;
          }
          return orderJobs(jobList)[0]?.key || '';
        });

        const top5Entries = await Promise.all(
          jobList.map(async (job) => {
            const payload = await fetchOpsTop5(job.key);
            return [job.key, payload];
          })
        );

        if (!mounted) {
          return;
        }

        setTop5Map(Object.fromEntries(top5Entries));
        setLoading(false);
      } catch (error) {
        if (!mounted) {
          return;
        }
        setApiOk(false);
        setApiMessage(error.message || 'ops-api-unavailable');
        setLoading(false);
      }
    }

    refreshDashboard();
    const timer = window.setInterval(refreshDashboard, 3000);

    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
  }, []);

  const anyRunning = useMemo(() => Object.values(jobs).some((job) => job?.running), [jobs]);
  const selectedJob = selectedJobKey ? normalizeJobMeta(jobs[selectedJobKey] || { key: selectedJobKey }) : null;

  async function handleRun(jobKey) {
    setActionError('');
    try {
      const result = await runOpsJob(jobKey);
      setJobs((current) => ({
        ...current,
        [jobKey]: result.state,
      }));
    } catch (error) {
      setActionError(error.message || 'run-failed');
    }
  }

  if (loading) {
    return <section className="panel">正在连接操作服务...</section>;
  }

  return (
    <section className="content-stack">
      <article className="panel panel-intro ops-intro">
        <div>
          <h2>操作界面</h2>
          <p>从统一入口选择策略执行，本地脚本完成后会自动刷新当天 Top5 候选内容。</p>
          <p className="panel-meta-line">当前后端主要提供单票过滤与评分，尚未加入组合层和市场层风控约束。</p>
          <p className="panel-meta-line">操作服务地址：{getOpsApiBase()}</p>
        </div>
        <div className="ops-summary">
          <div className="summary-item">
            <span>服务状态</span>
            <strong>{apiOk ? '已连接' : '未连接'}</strong>
          </div>
          <div className="summary-item">
            <span>运行状态</span>
            <strong>{anyRunning ? '任务执行中' : '空闲'}</strong>
          </div>
        </div>
      </article>

      {!apiOk && (
        <article className="freshness-banner freshness-danger">
          <strong>未连接到本地操作服务</strong>
          <p>请先在项目根目录执行 `npm run api`，然后刷新当前页面。</p>
          <p>错误信息：{apiMessage}</p>
        </article>
      )}

      {actionError && <article className="panel error">{actionError}</article>}

      <article className="panel ops-launcher">
        <div>
          <h3>统一策略入口</h3>
          <p className="panel-meta-line">选择要执行的策略，使用同一个入口发起任务。</p>
        </div>
        <div className="ops-launcher-form">
          <label className="ops-launcher-copy" htmlFor="strategy-select">
            当前策略
          </label>
          <select
            id="strategy-select"
            className="ops-select"
            value={selectedJobKey}
            onChange={(event) => setSelectedJobKey(event.target.value)}
            disabled={!apiOk || orderedJobs.length === 0 || anyRunning}
          >
            {orderedJobs.map((job) => (
              <option key={job.key} value={job.key}>
                {job.title}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="action-button action-primary"
            onClick={() => selectedJobKey && handleRun(selectedJobKey)}
            disabled={!apiOk || !selectedJobKey || jobs[selectedJobKey]?.running || anyRunning}
          >
            {selectedJob?.running ? '执行中...' : selectedJob ? `执行${selectedJob.title}` : '选择策略后执行'}
          </button>
        </div>
        {selectedJob && <p className="panel-meta-line">{selectedJob.hint}</p>}
      </article>

      <div className="ops-grid">
        {orderedJobs.map((job) => {
          const state = jobs[job.key] || { status: 'idle', output: [], running: false, settlementSummary: null };
          const top5 = top5Map[job.key] || { exists: false, csvText: '', markdown: '' };
          const rows = parseCsv(top5.csvText).slice(0, 5);
          const generatedAt = extractMeta(top5.markdown, '生成时间');
          const dataState = extractMeta(top5.markdown, '数据状态');
          const tradeDate = rows[0]?.trade_date || rows[0]?.tradeDate || '-';
          const settlement = state.settlementSummary;

          return (
            <article key={job.key} className="panel ops-card">
              <div className="ops-card-head">
                <div>
                  <h3>{job.title}</h3>
                  <p className="panel-meta-line">{job.hint}</p>
                </div>
                <span className={statusClass(state.status)}>{formatState(state.status)}</span>
              </div>

              <div className="ops-actions">
                <button
                  type="button"
                  className="action-button action-primary"
                  onClick={() => handleRun(job.key)}
                  disabled={!apiOk || state.running}
                >
                  {state.running ? '执行中...' : `执行${job.title}`}
                </button>
              </div>

              <div className="ops-meta-grid">
                <div className="meta-card">
                  <div className="meta-key">开始时间</div>
                  <div className="meta-value">{state.startedAt || '-'}</div>
                </div>
                <div className="meta-card">
                  <div className="meta-key">结束时间</div>
                  <div className="meta-value">{state.finishedAt || '-'}</div>
                </div>
                <div className="meta-card">
                  <div className="meta-key">退出码</div>
                  <div className="meta-value">{state.exitCode ?? '-'}</div>
                </div>
              </div>

              <div className="ops-log">
                <div className="ops-section-title">执行日志</div>
                <pre className="log-box">{(state.output || []).join('\n') || '暂无日志输出。'}</pre>
              </div>

              {settlement && (
                <div className="ops-log">
                  <div className="ops-section-title">自动结算</div>
                  <div className="ops-meta-grid compact-grid">
                    <div className="meta-card">
                      <div className="meta-key">结算状态</div>
                      <div className="meta-value">{settlement.status || '-'}</div>
                    </div>
                    <div className="meta-card">
                      <div className="meta-key">结算日期</div>
                      <div className="meta-value">{settlement.currentTradeDate || '-'}</div>
                    </div>
                    <div className="meta-card">
                      <div className="meta-key">截止买入日</div>
                      <div className="meta-value">{settlement.cutoffBuyDate || '-'}</div>
                    </div>
                    <div className="meta-card">
                      <div className="meta-key">新增结算</div>
                      <div className="meta-value">{settlement.settledCount ?? 0}</div>
                    </div>
                    <div className="meta-card">
                      <div className="meta-key">待补价文件</div>
                      <div className="meta-value">{settlement.pendingFileCount ?? 0}</div>
                    </div>
                    <div className="meta-card">
                      <div className="meta-key">迁移 sidecar</div>
                      <div className="meta-value">{settlement.migratedCount ?? 0}</div>
                    </div>
                  </div>

                  {settlement.message && <p className="panel-meta-line">{settlement.message}</p>}

                  {Array.isArray(settlement.settledFiles) && settlement.settledFiles.length > 0 && (
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>文件</th>
                            <th>买入日</th>
                            <th>已结算</th>
                            <th>待补价</th>
                            <th>尾盘价</th>
                            <th>收盘回退</th>
                          </tr>
                        </thead>
                        <tbody>
                          {settlement.settledFiles.map((item) => (
                            <tr key={`${job.key}-${item.fileName}`}>
                              <td>{item.fileName}</td>
                              <td>{item.buyDate}</td>
                              <td>{item.settledRows}</td>
                              <td>{item.pendingRows}</td>
                              <td>{item.tailPriceRows}</td>
                              <td>{item.dailyCloseRows}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {Array.isArray(settlement.skippedFiles) && settlement.skippedFiles.length > 0 && (
                    <div className="freshness-banner freshness-warn">
                      <strong>部分文件未纳入自动结算</strong>
                      {settlement.skippedFiles.slice(0, 3).map((item) => (
                        <p key={`${job.key}-${item.fileName || item.reason}`}>{`${item.fileName || 'unknown'}: ${item.reason}`}</p>
                      ))}
                    </div>
                  )}
                </div>
              )}

              <div className="ops-top5">
                <div className="ops-section-title">当天 Top5</div>
                {top5.exists ? (
                  <>
                    <div className="ops-meta-grid compact-grid">
                      <div className="meta-card">
                        <div className="meta-key">生成时间</div>
                        <div className="meta-value">{generatedAt}</div>
                      </div>
                      <div className="meta-card">
                        <div className="meta-key">行情日期</div>
                        <div className="meta-value">{tradeDate}</div>
                      </div>
                      <div className="meta-card">
                        <div className="meta-key">数据状态</div>
                        <div className="meta-value">{dataState}</div>
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
                          </tr>
                        </thead>
                        <tbody>
                          {rows.map((row) => (
                            <tr key={`${job.key}-${row.rank}-${row.code}`}>
                              <td>{row.rank}</td>
                              <td>{row.code}</td>
                              <td>{row.name}</td>
                              <td className="industry-col">{row.industry}</td>
                              <td>{row.score_100 || row.score100 || '-'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                ) : (
                  <div className="freshness-banner freshness-warn">
                    <strong>尚未读取到 Top5 文件</strong>
                    <p>先执行一次当前模型，完成后这里会自动刷新。</p>
                  </div>
                )}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}