import React from 'react';
import { useEffect, useState } from 'react';
import { loadMyPlan } from '../lib/myPlan';

export default function MyPlanPage() {
  const [plan, setPlan] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let mounted = true;

    loadMyPlan()
      .then((data) => {
        if (!mounted) {
          return;
        }
        setPlan(data);
        setLoading(false);
      })
      .catch((err) => {
        if (!mounted) {
          return;
        }
        setError(err.message || '加载 myplan 失败');
        setLoading(false);
      });

    return () => {
      mounted = false;
    };
  }, []);

  if (loading) {
    return <section className="panel">正在加载...</section>;
  }

  if (error) {
    return <section className="panel error">{error}</section>;
  }

  if (!plan) {
    return <section className="panel">未读取到计划数据。</section>;
  }

  return (
    <section className="content-stack">
      <article className="panel panel-intro">
        <div>
          <h2>{plan.title}</h2>
          <p>来自仓库根目录 `myplan.md`。</p>
        </div>
        <div className="meta-grid">
          {plan.metadata.map((item) => (
            <div key={item.key} className="meta-card">
              <div className="meta-key">{item.key}</div>
              <div className="meta-value">{item.value}</div>
            </div>
          ))}
        </div>
      </article>

      <article className="panel">
        <h3>开仓明细</h3>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {plan.table.headers.map((header) => (
                  <th key={header}>{header}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {plan.table.rows.map((row) => {
                const code = row['代码'] || '';
                const rank = row['排名'] || '';
                const rowKey = `${rank}-${code}`;

                return (
                  <tr key={rowKey}>
                    {plan.table.headers.map((header) => (
                      <td key={`${rowKey}-${header}`}>{row[header]}</td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </article>

      <article className="panel">
        <h3>资金汇总</h3>
        <div className="summary-grid">
          {plan.summary.map((item) => (
            <div key={item.key} className="summary-item">
              <span>{item.key}</span>
              <strong>{item.value}</strong>
            </div>
          ))}
        </div>
      </article>
    </section>
  );
}
