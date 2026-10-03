import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';
import react from '@vitejs/plugin-react';

const readJson = (path) => JSON.parse(readFileSync(new URL(path, import.meta.url), 'utf8'));

test('desktop remains a private linked npm workspace', () => {
  const root = readJson('../package.json');
  const desktop = readJson('../apps/desktop/package.json');
  const lock = readJson('../package-lock.json');
  assert.equal(root.private, true);
  assert.deepEqual(root.workspaces, ['apps/desktop']);
  assert.equal(desktop.private, true);
  assert.equal(desktop.name, '@phoenix/desktop');
  assert.equal(desktop.type, 'module');
  assert.equal(lock.packages['node_modules/@phoenix/desktop'].link, true);
  assert.equal(lock.packages['node_modules/@phoenix/desktop'].resolved, 'apps/desktop');
});

test('foundation screen renders semantic text without interactive feature controls', () => {
  const source = readFileSync(new URL('../apps/desktop/src/App.tsx', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS },
  });
  const exports = {};
  runInNewContext(outputText, { exports, require: createRequire(import.meta.url) });
  const markup = renderToStaticMarkup(exports.default());
  assert.match(markup, /<main aria-labelledby="phoenix-title">/);
  assert.match(markup, /<h1 id="phoenix-title">PHOENIX<\/h1>/);
  assert.match(markup, /Rise from the ashes\./);
  assert.match(markup, /Desktop foundation operational\./);
  assert.doesNotMatch(markup, /<(button|input|nav|textarea)\b/);
});

test('desktop has no IPC permissions and keeps a restrictive production CSP', () => {
  const config = readJson('../apps/desktop/src-tauri/tauri.conf.json');
  assert.deepEqual(config.app.security.capabilities.flatMap((capability) => capability.permissions), []);
  assert.deepEqual(config.app.security.capabilities.flatMap((capability) => capability.windows), ['main']);
  assert.equal(config.app.withGlobalTauri ?? false, false);
  assert.deepEqual(config.plugins ?? {}, {});
  assert.match(config.app.security.csp, /connect-src 'none'/);
  assert.match(config.app.security.csp, /object-src 'none'/);
  assert.doesNotMatch(config.app.security.csp, /unsafe-inline|unsafe-eval|\*/);
});

test('development CSP allows only the React refresh preamble as an inline script', () => {
  const config = readJson('../apps/desktop/src-tauri/tauri.conf.json');
  const preamble = react.preambleCode.replace('__BASE__', '/');
  const hash = createHash('sha256').update(preamble).digest('base64');
  const scriptPolicy = config.app.security.devCsp.split(';').find((directive) => directive.trim().startsWith('script-src'));
  assert.ok(scriptPolicy.includes(`'sha256-${hash}'`));
  assert.doesNotMatch(scriptPolicy, /unsafe-inline|unsafe-eval|\*/);
});
