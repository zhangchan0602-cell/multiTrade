import React from 'react';
import KechuangPage from './KechuangPage';
import { calculateIndexMarket } from '../lib/kechuang';
import { refreshSemiconductorIndex } from '../lib/opsApi';

const SEMICONDUCTOR_INDEX = {
  indexCode: 'H30184.CSI',
  indexName: '中证半导体',
};

function calculateSemiconductorMarket(csvText) {
  return calculateIndexMarket(csvText, SEMICONDUCTOR_INDEX);
}

export default function SemiconductorPage() {
  return (
    <KechuangPage
      title="半导体"
      analysisName="中证半导体"
      description="基于中证半导体指数日线，计算当前上涨概率、回撤概率与本轮趋势压力位。"
      emptyHeading="半导体模型"
      refreshIndex={refreshSemiconductorIndex}
      calculateMarket={calculateSemiconductorMarket}
    />
  );
}
