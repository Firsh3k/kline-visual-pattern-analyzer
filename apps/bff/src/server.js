'use strict';
const { loadConfig, ConfigError } = require('./config');
const { createApp } = require('./app');

let config;
try {
  config = loadConfig();
} catch (e) {
  if (e instanceof ConfigError) {
    console.error(`配置错误：${e.message}`);
    process.exit(2);
  }
  throw e;
}
const app = createApp(config);
const server = app.listen(config.port, config.host, () => {
  console.log(JSON.stringify({ event: 'listening', port: config.port, env: config.deployEnv,
    python: new URL(config.pythonBase).host }));
});
server.headersTimeout = 20000;
server.requestTimeout = 60000;
const stop = () => server.close(() => process.exit(0));
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
