'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const releaseIdentity = require('../release_identity.js');

function testValidMetadata() {
  const metadata = {
    schemaVersion: 1,
    version: 'v5.0.1',
    commit: '0123456789abcdef0123456789abcdef01234567',
    releasedAt: '2026-10-07',
    environment: 'production',
    rollbackBaseline: 'v5.0.0',
    repository: 'Firsh3k/kline-visual-pattern-analyzer'
  };
  const validation = releaseIdentity.validateReleaseInfo(metadata);
  assert.strictEqual(validation.ok, true, validation.errors.join(', '));

  const display = releaseIdentity.makeDisplay(metadata);
  assert.strictEqual(display.verified, true);
  assert.strictEqual(display.version, 'v5.0.1');
  assert.strictEqual(display.commit, metadata.commit);
  assert.strictEqual(display.commitShort, metadata.commit.slice(0, 7));
  assert.strictEqual(display.releasedAt, '2026-10-07');
  assert.strictEqual(display.environment, 'Production');
  assert.strictEqual(display.rollbackBaseline, 'v5.0.0');
  assert.strictEqual(
    display.releaseUrl,
    'https://github.com/Firsh3k/kline-visual-pattern-analyzer/releases/tag/v5.0.1'
  );
}

function testFailClosedMetadata() {
  const cases = [
    null,
    {},
    {
      schemaVersion: 1,
      version: '5.0.1',
      commit: '0123456789abcdef0123456789abcdef01234567',
      releasedAt: '2026-10-07',
      environment: 'production',
      rollbackBaseline: 'v5.0.0',
      repository: 'Firsh3k/kline-visual-pattern-analyzer'
    },
    {
      schemaVersion: 1,
      version: 'v5.0.1',
      commit: 'shortsha',
      releasedAt: '2026-10-07',
      environment: 'production',
      rollbackBaseline: 'v5.0.0',
      repository: 'Firsh3k/kline-visual-pattern-analyzer'
    },
    {
      schemaVersion: 1,
      version: 'v5.0.1',
      commit: '0123456789abcdef0123456789abcdef01234567',
      releasedAt: '2026-02-30',
      environment: 'production',
      rollbackBaseline: 'v5.0.0',
      repository: 'Firsh3k/kline-visual-pattern-analyzer'
    }
  ];

  for (const metadata of cases) {
    const display = releaseIdentity.makeDisplay(metadata);
    assert.strictEqual(display.verified, false);
    assert.strictEqual(display.version, 'Version Unknown');
    assert.strictEqual(display.commitShort, '—');
    assert.strictEqual(display.releaseUrl, null);
  }
}

function testRepositoryIntegration() {
  const root = path.resolve(__dirname, '..');
  const html = fs.readFileSync(path.join(root, 'kline_analyzer.html'), 'utf8');
  const template = JSON.parse(fs.readFileSync(path.join(root, 'release-info.json'), 'utf8'));

  assert(html.includes('release_identity.js'), 'HTML must load canonical release identity helper');
  assert(html.includes("fetch('release-info.json'"), 'HTML must fetch canonical release metadata');
  assert(html.includes('Version Unknown'), 'HTML must have an explicit fail-closed placeholder');
  assert(!html.includes('const DEPLOYMENT_INFO'), 'HTML must not keep a second hard-coded release source');
  assert.strictEqual(
    releaseIdentity.makeDisplay(template).version,
    'Version Unknown',
    'unreleased repository template must never masquerade as a production release'
  );
}

function testPrereleaseOnlyOutsideProduction() {
  const base = {
    schemaVersion: 1, version: 'v6.0.0-rc.1', commit: '0123456789abcdef0123456789abcdef01234567',
    releasedAt: '2026-10-14', rollbackBaseline: 'v5.0.1', repository: 'Firsh3k/kline-visual-pattern-analyzer'
  };
  assert.strictEqual(releaseIdentity.validateReleaseInfo({ ...base, environment: 'staging' }).ok, true);
  assert.strictEqual(releaseIdentity.makeDisplay({ ...base, environment: 'staging' }).version, 'v6.0.0-rc.1');
  assert.strictEqual(releaseIdentity.validateReleaseInfo({ ...base, environment: 'production' }).ok, false);
  assert.strictEqual(releaseIdentity.makeDisplay({ ...base, environment: 'production' }).version, 'Version Unknown');
  assert.strictEqual(releaseIdentity.validateReleaseInfo({ ...base, version: 'v6.0.0-beta', environment: 'staging' }).ok, false);
}

testValidMetadata();
testFailClosedMetadata();
testPrereleaseOnlyOutsideProduction();
testRepositoryIntegration();
console.log('FIR-28 release identity tests passed');
