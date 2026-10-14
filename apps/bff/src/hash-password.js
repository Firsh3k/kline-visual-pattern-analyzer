'use strict';
// 生成 LOGIN_PASSWORD_HASH：  node src/hash-password.js   （从标准输入读取密码，不进 shell 历史）
const { hashPassword } = require('./security');

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (d) => { input += d; });
process.stdin.on('end', () => {
  const pw = input.replace(/\r?\n$/, '');
  if (pw.length < 12) { console.error('密码至少 12 个字符'); process.exit(2); }
  console.log(hashPassword(pw));
});
