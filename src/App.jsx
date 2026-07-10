import React from 'react';
import { NavLink, Navigate, Route, Routes } from 'react-router-dom';
import BacktestPage from './pages/BacktestPage';
import KechuangPage from './pages/KechuangPage';
import IndustryTrendPage from './pages/IndustryTrendPage';
import OpsPage from './pages/OpsPage';
import Top20Page from './pages/Top20Page';
import MyPlanPage from './pages/MyPlanPage';

const navClassName = ({ isActive }) => (isActive ? 'nav-link nav-link-active' : 'nav-link');

export default function App() {
  return (
    <div className="app-root">
      <header className="hero">
        <div className="hero-text">
          <h1>多因子榜单</h1>
          <p className="hero-subtitle">覆盖评分、历史走势，以及本期新增/移出追踪。</p>
        </div>
        <nav className="nav-tabs">
          <NavLink to="/ops" className={navClassName}>
            操作界面
          </NavLink>
          <NavLink to="/top20" className={navClassName}>
            榜单
          </NavLink>
          <NavLink to="/backtest" className={navClassName}>
            回测
          </NavLink>
          <NavLink to="/kechuang" className={navClassName}>
            科创
          </NavLink>
          <NavLink to="/industry-trends" className={navClassName}>
            行业趋势
          </NavLink>
          <NavLink to="/myplan" className={navClassName}>
            MyPlan
          </NavLink>
        </nav>
      </header>

      <main className="page-body">
        <Routes>
          <Route path="/" element={<Navigate to="/top20" replace />} />
          <Route path="/ops" element={<OpsPage />} />
          <Route path="/top20" element={<Top20Page />} />
          <Route path="/backtest" element={<BacktestPage />} />
          <Route path="/kechuang" element={<KechuangPage />} />
          <Route path="/industry-trends" element={<IndustryTrendPage />} />
          <Route path="/myplan" element={<MyPlanPage />} />
        </Routes>
      </main>
    </div>
  );
}
