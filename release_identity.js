(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.ReleaseIdentity = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const VERSION_RE = /^v\d+\.\d+\.\d+$/;
  const COMMIT_RE = /^[0-9a-f]{40}$/i;
  const REPOSITORY_RE = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
  const ALLOWED_ENVIRONMENTS = new Set(['production', 'staging', 'development']);

  function isIsoDate(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const parsed = new Date(value + 'T00:00:00Z');
    return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
  }

  function validateReleaseInfo(info) {
    const errors = [];
    if (!info || typeof info !== 'object' || Array.isArray(info)) {
      return { ok: false, errors: ['metadata must be an object'] };
    }

    if (info.schemaVersion !== 1) errors.push('schemaVersion must equal 1');
    if (!VERSION_RE.test(info.version || '')) errors.push('version must be vMAJOR.MINOR.PATCH');
    if (!COMMIT_RE.test(info.commit || '')) errors.push('commit must be a full 40-character SHA');
    if (!isIsoDate(info.releasedAt)) errors.push('releasedAt must be a valid YYYY-MM-DD date');
    if (!ALLOWED_ENVIRONMENTS.has(info.environment)) errors.push('environment is invalid');
    if (!VERSION_RE.test(info.rollbackBaseline || '')) errors.push('rollbackBaseline must be a release tag');
    if (!REPOSITORY_RE.test(info.repository || '')) errors.push('repository must be owner/name');

    return { ok: errors.length === 0, errors };
  }

  function titleCase(value) {
    return value ? value.charAt(0).toUpperCase() + value.slice(1) : 'Unknown';
  }

  function unknownDisplay() {
    return {
      verified: false,
      version: 'Version Unknown',
      commit: '—',
      commitShort: '—',
      releasedAt: '—',
      environment: 'Unknown',
      rollbackBaseline: '—',
      releaseUrl: null
    };
  }

  function makeDisplay(info) {
    const validation = validateReleaseInfo(info);
    if (!validation.ok) return unknownDisplay();

    return {
      verified: true,
      version: info.version,
      commit: info.commit,
      commitShort: info.commit.slice(0, 7),
      releasedAt: info.releasedAt,
      environment: titleCase(info.environment),
      rollbackBaseline: info.rollbackBaseline,
      releaseUrl: 'https://github.com/' + info.repository + '/releases/tag/' + encodeURIComponent(info.version)
    };
  }

  return {
    validateReleaseInfo,
    makeDisplay,
    unknownDisplay
  };
});
