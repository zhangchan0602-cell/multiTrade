const API_BASE = import.meta.env.VITE_OPS_API_BASE || 'http://127.0.0.1:8787';

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
    ...options,
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch (error) {
    payload = null;
  }

  if (!response.ok) {
    const message = payload?.error || payload?.reason || `request-failed:${response.status}`;
    throw new Error(message);
  }

  return payload;
}

export function getOpsApiBase() {
  return API_BASE;
}

export function fetchOpsHealth() {
  return request('/api/health');
}

export function fetchOpsJobs() {
  return request('/api/jobs');
}

export function runOpsJob(jobKey) {
  return request(`/api/run/${jobKey}`, { method: 'POST' });
}

export function fetchOpsTop5(jobKey) {
  return request(`/api/top5/${jobKey}`);
}

export function fetchCombinedBoard() {
  return request('/api/combined');
}

export function generateCombinedBoard() {
  return request('/api/combined/generate', { method: 'POST' });
}

export function refreshKechuangIndex() {
  return request('/api/kechuang/refresh', { method: 'POST' });
}

export function refreshSemiconductorIndex() {
  return request('/api/semiconductor/refresh', { method: 'POST' });
}

export function fetchIndustryTrendRank() {
  return request('/api/industry-trends');
}

export function refreshIndustryTrendRank() {
  return request('/api/industry-trends/refresh', { method: 'POST' });
}
