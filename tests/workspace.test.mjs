import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
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

const appSource = readFileSync(new URL('../apps/desktop/src/App.tsx', import.meta.url), 'utf8');
const css = readFileSync(new URL('../apps/desktop/src/styles.css', import.meta.url), 'utf8');
const appTree = ts.createSourceFile('App.tsx', appSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function nodes(predicate, root = appTree) {
  const found = [];
  function visit(node) {
    if (predicate(node)) found.push(node);
    ts.forEachChild(node, visit);
  }
  visit(root);
  return found;
}
function loadApp() {
  const { outputText } = ts.transpileModule(appSource, {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS },
  });
  const exports = {};
  runInNewContext(outputText, { exports, require: createRequire(import.meta.url) });
  return exports;
}
const app = loadApp();
const expectedViews = [
  ['onboarding', 'Onboarding'], ['home', 'Home / Chat'],
  ['agents', 'Agent Network'], ['tasks', 'Task Graph'], ['memory', 'Memory'],
  ['files', 'Files / Documents'], ['computer', 'Computer Control'],
  ['research', 'Research'], ['media', 'Media'], ['permissions', 'Permissions'],
  ['models', 'Models'], ['voice', 'Voice'], ['settings', 'Settings'],
  ['observability', 'Observability / Developer'],
];

test('shell has exactly fourteen canonical route IDs and labels', () => {
  assert.deepEqual(Array.from(app.views, (view) => [view.id, view.label]), expectedViews);
  assert.equal(new Set(app.views.map((view) => view.id)).size, 14);
  for (const [id, label] of expectedViews) {
    const view = app.resolveView(id);
    assert.equal(view.label, label);
    assert.ok(['Mission', 'Workspace', 'System'].includes(view.group));
    assert.ok(view.purpose.length > 30);
    assert.equal(app.views.filter((item) => item.id === id).length, 1);
  }
});

test('unknown route IDs safely resolve to home without string coercion', () => {
  for (const value of [undefined, null, '', 'HOME', ' home ', 'unknown', 1, {}, '__proto__', '<script>']) {
    assert.equal(app.resolveView(value).id, 'home');
  }
  assert.equal(app.resolveView({ toString() { throw new Error('must not coerce'); } }).id, 'home');
});

test('React SSR renders the semantic branded shell and selected home navigation', () => {
  const markup = renderToStaticMarkup(createElement(app.default));
  assert.match(markup, /<h1>PHOENIX<\/h1>/);
  assert.match(markup, /Rise from the ashes\./);
  assert.match(markup, /<nav aria-label="Primary navigation">/);
  assert.match(markup, /<main id="main-content" tabindex="-1" aria-labelledby="page-title">/);
  assert.match(markup, /<h2 id="page-title">Home \/ Chat<\/h2>/);
  assert.match(markup, /<a class="skip-link" href="#main-content">Skip to main content<\/a>/);
  const buttons = [...markup.matchAll(/<button\b([^>]*)>([^<]+)<\/button>/g)];
  assert.equal(buttons.length, 14);
  assert.equal(buttons.filter((button) => button[1].includes('aria-current="page"')).length, 1);
  assert.equal(buttons.find((button) => button[1].includes('aria-current="page"'))[2], 'Home / Chat');
  for (const [, label] of expectedViews) {
    const button = buttons.find((item) => item[2] === label);
    assert.ok(button, label);
    assert.match(button[1], /type="button"/);
    assert.doesNotMatch(button[1], /disabled|tabindex="-1"|role=/);
  }
});

test('every future workspace renders its own purpose and an honest placeholder', () => {
  for (const [id, label] of expectedViews.filter(([id]) => id !== 'home')) {
    const view = app.resolveView(id);
    const markup = renderToStaticMarkup(createElement(app.ViewPanel, { view }));
    assert.ok(markup.includes(`${label} workspace`));
    assert.ok(markup.includes(view.purpose));
    assert.match(markup, /This workspace is not connected yet\./);
    assert.doesNotMatch(markup, /<(button|input|textarea|iframe)\b|role="alert"/);
  }
});

test('home previews product areas without fake operations or microphone prerequisites', () => {
  const markup = renderToStaticMarkup(createElement(app.default));
  assert.match(markup, /Interface shell — backend integration pending/);
  assert.match(markup, /Conversation and live workspace connections are coming next/);
  assert.match(markup, /without enabling a microphone/);
  assert.match(markup, /Navigation is ready\. Workspace connections are pending/);
  assert.doesNotMatch(markup, /backend online|agents online|voice listening|tasks running|permissions approved|memory synced|models installed|Desktop foundation operational/i);
  assert.doesNotMatch(markup, /<(input|textarea|form)\b|Send message|Start listening|Approve|Deny/);
});

test('selection wiring updates state, title, page content and current-page evidence', () => {
  // SSR is not a GUI click test: inspect the actual handler/state expressions instead.
  const state = nodes(ts.isCallExpression).find((node) => node.expression.getText(appTree) === 'useState<ViewId>' || node.expression.getText(appTree) === 'useState');
  assert.ok(state);
  assert.equal(state.arguments[0].getText(appTree), "'home'");
  const navigate = nodes(ts.isFunctionDeclaration).find((node) => node.name?.text === 'navigate');
  assert.ok(navigate);
  const calls = nodes(ts.isCallExpression, navigate).map((node) => node.getText(appTree));
  assert.ok(calls.includes('resolveView(value)'));
  assert.ok(calls.includes('setSelectedView(nextView)'));
  const click = nodes(ts.isJsxAttribute).find((node) => node.name.getText(appTree) === 'onClick');
  assert.equal(click.initializer.getText(appTree), '{() => navigate(view.id)}');
  const current = nodes(ts.isJsxAttribute).find((node) => node.name.getText(appTree) === 'aria-current');
  assert.equal(current.initializer.getText(appTree), "{currentView.id === view.id ? 'page' : undefined}");
  assert.match(appSource, /const currentView = resolveView\(selectedView\)/);
  assert.match(appSource, /<h2 id="page-title">\{currentView.label\}<\/h2>/);
  assert.match(appSource, /<ViewPanel view=\{currentView\}/);
});

test('focus follows a committed navigation change without stealing initial focus', () => {
  const effect = nodes(ts.isCallExpression).find((node) => node.expression.getText(appTree) === 'useEffect');
  assert.ok(effect);
  assert.equal(effect.arguments[1].getText(appTree), '[selectedView]');
  const body = effect.arguments[0].getText(appTree);
  assert.match(body, /if \(focusRequested.current\)/);
  assert.match(body, /mainRef.current\?\.focus\(\)/);
  assert.match(body, /focusRequested.current = false/);
  assert.match(appSource, /const focusRequested = useRef\(false\)/);
  assert.match(appSource, /focusRequested.current = true;\s+setSelectedView\(nextView\)/);
  assert.match(appSource, /ref=\{mainRef\} tabIndex=\{-1\}/);
  assert.doesNotMatch(appSource, /onKeyDown|onKeyUp|addEventListener|setTimeout|setInterval/);
});

test('shell has no backend, native bridge, storage or unsafe content execution', () => {
  const imports = nodes(ts.isImportDeclaration);
  assert.equal(imports.length, 1);
  assert.equal(imports[0].moduleSpecifier.text, 'react');
  const allowedCalls = new Set(['useState', 'useRef', 'useEffect', 'resolveView', 'setSelectedView', 'navigate', 'views.find', 'groups.map', 'views.filter', 'mainRef.current?.focus']);
  for (const call of nodes(ts.isCallExpression)) {
    const name = call.expression.getText(appTree);
    assert.ok(allowedCalls.has(name) || name === 'views.filter((view) => view.group === group).map', name);
  }
  const banned = new Set(['fetch', 'WebSocket', 'XMLHttpRequest', 'invoke', 'eval', 'Function', 'localStorage', 'sessionStorage', 'navigator', 'window', 'document', 'globalThis', 'require', 'dangerouslySetInnerHTML']);
  for (const node of nodes(ts.isIdentifier)) assert.ok(!banned.has(node.text), node.text);
  assert.equal(nodes(ts.isNewExpression).length, 0);
  assert.doesNotMatch(appSource, /@tauri|ollama|colibri|sqlite|https?:\/\/|import\(/i);
  for (const attribute of nodes(ts.isJsxAttribute)) {
    assert.ok(!['style', 'src', 'srcDoc', 'dangerouslySetInnerHTML'].includes(attribute.name.getText(appTree)));
  }
});

test('CSS preserves reachable navigation, narrow layouts, focus and reduced motion', () => {
  assert.match(css, /grid-template-columns:\s*224px minmax\(0, 1fr\)/);
  assert.match(css, /overflow-y:\s*auto/);
  for (const width of [1000, 680, 460]) assert.ok(css.includes(`@media (max-width: ${width}px)`));
  assert.match(css, /@media \(max-width: 680px\)[\s\S]*position: static; height: auto; overflow-y: visible/);
  assert.match(css, /min-width:\s*0/);
  assert.match(css, /overflow-wrap:\s*anywhere/);
  assert.match(css, /:focus-visible\s*\{[^}]*outline:\s*3px solid var\(--accent\)/);
  assert.match(css, /\.skip-link:focus\s*\{[^}]*translateY\(0\)/);
  assert.match(css, /\.nav-button\[aria-current="page"\]/);
  assert.match(css, /prefers-reduced-motion: reduce/);
  assert.doesNotMatch(css, /@import|url\(|@font-face|overflow-x:\s*hidden/);
});

test('text and selected navigation tokens meet readable contrast', () => {
  const tokens = Object.fromEntries([...css.matchAll(/--([\w-]+):\s*(#[\da-f]{6});/g)].map((match) => [match[1], match[2]]));
  function luminance(hex) {
    const channels = [1, 3, 5].map((offset) => parseInt(hex.slice(offset, offset + 2), 16) / 255);
    const [r, g, b] = channels.map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }
  for (const [foreground, background] of [['text', 'background'], ['muted', 'surface'], ['muted', 'sidebar'], ['accent', 'accent-surface'], ['background', 'accent']]) {
    const values = [luminance(tokens[foreground]), luminance(tokens[background])].sort((a, b) => b - a);
    assert.ok((values[0] + 0.05) / (values[1] + 0.05) >= 4.5, `${foreground}/${background}`);
  }
});

test('PHX-080 leaves manifests, entry point and Tauri native security files byte-identical', () => {
  const root = fileURLToPath(new URL('../', import.meta.url));
  for (const path of ['package.json', 'package-lock.json', 'pyproject.toml', 'uv.lock', 'apps/desktop/package.json', 'apps/desktop/src/main.tsx', 'apps/desktop/tsconfig.json', 'apps/desktop/vite.config.ts', 'apps/desktop/src-tauri/Cargo.toml', 'apps/desktop/src-tauri/Cargo.lock', 'apps/desktop/src-tauri/tauri.conf.json', 'apps/desktop/src-tauri/src/main.rs']) {
    const actual = execFileSync('git', ['hash-object', '--path=' + path, path], { cwd: root, encoding: 'utf8' }).trim();
    const expected = execFileSync('git', ['rev-parse', '4a70867203a70d3a85e2c141ef0dbb92bdff42e2:' + path], { cwd: root, encoding: 'utf8' }).trim();
    assert.equal(actual, expected, path);
  }
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
