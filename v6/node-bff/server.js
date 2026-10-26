/**
 * FIR-31 V6 — Node.js Express BFF (App Builder)
 * 只做 API 网关和静态文件服务，所有算法调用 Python FastAPI Service
 * 架构: Frontend -> Node BFF -> Python Analysis Service -> Python Canonical Core
 */
const express = require('express');
const cors = require('cors');
const path = require('path');
const https = require('https');
const http = require('http');

const app = express();
const PORT = process.env.PORT || 3000;

// Python Analysis Service 地址
// 本地开发: http://localhost:8001
// 生产部署: 替换为实际公网地址
const PYTHON_SERVICE = process.env.PYTHON_SERVICE_URL || 'http://localhost:8001';

app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// 调用 Python Service
function callPython(pathname, options = {}) {
  return new Promise((resolve, reject) => {
    const url = new URL(pathname, PYTHON_SERVICE);
    const client = url.protocol === 'https:' ? https : http;
    const reqOptions = {
      hostname: url.hostname,
      port: url.port,
      path: url.pathname + url.search,
      method: options.method || 'GET',
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    };
    const req = client.request(reqOptions, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          resolve({ status: res.statusCode, data: JSON.parse(data) });
        } catch (e) {
          resolve({ status: res.statusCode, data: { detail: data } });
        }
      });
    });
    req.on('error', reject);
    if (options.body) req.write(JSON.stringify(options.body));
    req.end();
  });
}

// ============ API 端点（透传到 Python Service） ============

app.get('/api/health', async (req, res) => {
  try {
    const py = await callPython('/health');
    res.json({
      status: 'ok',
      version: '6.0.0',
      architecture: 'Node BFF -> Python Service -> Canonical Core',
      python_service: PYTHON_SERVICE,
      python_status: py.data,
      time: new Date().toISOString()
    });
  } catch (e) {
    res.json({ status: 'degraded', version: '6.0.0', python_service: 'unreachable', error: e.message });
  }
});

app.get('/api/analyze/:code', async (req, res) => {
  try {
    const days = req.query.days || 370;
    const py = await callPython(`/analyze/${req.params.code}?days=${days}`);
    res.status(py.status).json(py.data);
  } catch (e) {
    res.status(500).json({ detail: 'Python Service 调用失败: ' + e.message });
  }
});

app.get('/api/market-env', async (req, res) => {
  try {
    const py = await callPython('/market-env');
    res.status(py.status).json(py.data);
  } catch (e) {
    res.status(500).json({ detail: 'Python Service 调用失败: ' + e.message });
  }
});

app.get('/api/watchlist', async (req, res) => {
  try {
    const py = await callPython('/watchlist');
    res.json(py.data);
  } catch (e) {
    res.status(500).json({ detail: e.message });
  }
});

app.post('/api/watchlist/:code', async (req, res) => {
  try {
    const name = req.query.name ? `?name=${encodeURIComponent(req.query.name)}` : '';
    const py = await callPython(`/watchlist/${req.params.code}${name}`, { method: 'POST' });
    res.json(py.data);
  } catch (e) {
    res.status(500).json({ detail: e.message });
  }
});

app.delete('/api/watchlist/:code', async (req, res) => {
  try {
    const py = await callPython(`/watchlist/${req.params.code}`, { method: 'DELETE' });
    res.json(py.data);
  } catch (e) {
    res.status(500).json({ detail: e.message });
  }
});

app.post('/api/watchlist/analyze', async (req, res) => {
  try {
    const py = await callPython('/watchlist/analyze', { method: 'POST' });
    res.json(py.data);
  } catch (e) {
    res.status(500).json({ detail: e.message });
  }
});

app.post('/api/scan', async (req, res) => {
  try {
    const py = await callPython('/scan', { method: 'POST', body: req.body });
    res.json(py.data);
  } catch (e) {
    res.status(500).json({ detail: e.message });
  }
});

app.get('/api/sectors', async (req, res) => {
  try {
    const py = await callPython('/sectors');
    res.json(py.data);
  } catch (e) {
    res.status(500).json({ detail: e.message });
  }
});

app.post('/api/sector/:code/scan', async (req, res) => {
  try {
    const minScore = req.query.min_score || 60;
    const py = await callPython(`/sector/${req.params.code}/scan?min_score=${minScore}`, { method: 'POST' });
    res.json(py.data);
  } catch (e) {
    res.status(500).json({ detail: e.message });
  }
});

app.get('/api/sync/config', async (req, res) => {
  try {
    const py = await callPython('/sync/config');
    res.json(py.data);
  } catch (e) {
    res.status(500).json({ detail: e.message });
  }
});

app.post('/api/sync/pull', async (req, res) => {
  try {
    const py = await callPython('/sync/pull', { method: 'POST' });
    res.json(py.data);
  } catch (e) {
    res.status(500).json({ detail: e.message });
  }
});

// 前端页面
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

app.listen(PORT, () => {
  console.log('============================================================');
  console.log('K线形态分析工具 V6 — Node.js Express BFF');
  console.log('============================================================');
  console.log('架构: Frontend -> Node BFF -> Python Service -> Canonical Core');
  console.log('Python Service:', PYTHON_SERVICE);
  console.log('访问地址: http://localhost:' + PORT);
  console.log('============================================================');
});
