import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';
import { renderToStaticMarkup } from 'react-dom/server';
import * as React from 'react';
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
function loadApp(reactRuntime = React) {
  const { outputText } = ts.transpileModule(appSource, {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS },
  });
  const exports = {};
  const require = createRequire(import.meta.url);
  runInNewContext(outputText, { exports, Promise, require: (name) => name === 'react' ? reactRuntime : require(name) });
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
  const buttons = [...markup.match(/<nav aria-label="Primary navigation">([\s\S]*?)<\/nav>/)[1].matchAll(/<button\b([^>]*)>([^<]+)<\/button>/g)];
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

test('home chat stays honest without fake operations or microphone prerequisites', () => {
  const markup = renderToStaticMarkup(createElement(app.default));
  assert.match(markup, /Interface shell — backend integration pending/);
  assert.match(markup, /Not connected\. Backend chat integration is unavailable/);
  assert.match(markup, /without enabling a microphone/);
  assert.match(markup, /No messages supplied/);
  assert.doesNotMatch(markup, /backend online|agents online|voice listening|tasks running|permissions approved|memory synced|models installed|Desktop foundation operational/i);
  assert.match(markup, /<textarea[^>]*maxLength="4000"/);
  assert.match(markup, /<button[^>]*disabled=""[^>]*>Send<\/button>/);
  assert.doesNotMatch(markup, /Approve|Deny|<li class="message/);
});

test('selection wiring updates state, title, page content and current-page evidence', () => {
  // SSR is not a GUI click test: inspect the actual handler/state expressions instead.
  const appFunction = nodes(ts.isFunctionDeclaration).find((node) => node.name?.text === 'App');
  const state = nodes(ts.isCallExpression, appFunction).find((node) => node.expression.getText(appTree) === 'useState<ViewId>' || node.expression.getText(appTree) === 'useState');
  assert.ok(state);
  assert.equal(state.arguments[0].getText(appTree), "'home'");
  const navigate = nodes(ts.isFunctionDeclaration).find((node) => node.name?.text === 'navigate');
  assert.ok(navigate);
  const calls = nodes(ts.isCallExpression, navigate).map((node) => node.getText(appTree));
  assert.ok(calls.includes('resolveView(value)'));
  assert.ok(calls.includes('setSelectedView(nextView)'));
  const click = nodes(ts.isJsxAttribute, appFunction).find((node) => node.name.getText(appTree) === 'onClick');
  assert.equal(click.initializer.getText(appTree), '{() => navigate(view.id)}');
  const current = nodes(ts.isJsxAttribute).find((node) => node.name.getText(appTree) === 'aria-current');
  assert.equal(current.initializer.getText(appTree), "{currentView.id === view.id ? 'page' : undefined}");
  assert.match(appSource, /const currentView = resolveView\(selectedView\)/);
  assert.match(appSource, /<h2 id="page-title">\{currentView.label\}<\/h2>/);
  assert.match(appSource, /<ViewPanel view=\{currentView\}/);
});

test('focus follows a committed navigation change without stealing initial focus', () => {
  const appFunction = nodes(ts.isFunctionDeclaration).find((node) => node.name?.text === 'App');
  const effect = nodes(ts.isCallExpression, appFunction).find((node) => node.expression.getText(appTree) === 'useEffect');
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
  const allowedCalls = new Set(['useState', 'useRef', 'useEffect', 'resolveView', 'setSelectedView', 'navigate', 'views.find', 'groups.map', 'views.filter', 'mainRef.current?.focus', 'Object.getOwnPropertyDescriptor', 'Reflect.ownKeys', 'Array.isArray', 'Math.min', 'dataValue', 'readChatPort', 'validateDraft', 'boundedMessages', 'sendDraft', 'cancelWait', 'acknowledgedDraft', 'useChatDraft', 'setDraft', 'setPending', 'setFeedback', 'event.preventDefault', 'chat.setDraft', 'snapshot.messages.map', 'messages.some', 'messages.push', 'port.sendText', 'value.trim', 'id.trim', 'content.trim']);
  for (const call of nodes(ts.isCallExpression)) {
    const name = call.expression.getText(appTree);
    assert.ok(allowedCalls.has(name) || name === 'views.filter((view) => view.group === group).map', name);
  }
  const sendFunction = nodes(ts.isFunctionDeclaration).find((node) => node.name?.text === 'sendDraft');
  const suppliedCalls = nodes(ts.isCallExpression).filter((node) => node.expression.getText(appTree) === 'port.sendText');
  assert.equal(suppliedCalls.length, 1);
  assert.ok(nodes(ts.isCallExpression, sendFunction).includes(suppliedCalls[0]));
  const banned = new Set(['fetch', 'WebSocket', 'XMLHttpRequest', 'invoke', 'eval', 'Function', 'localStorage', 'sessionStorage', 'navigator', 'window', 'document', 'globalThis', 'require', 'dangerouslySetInnerHTML', 'getUserMedia', 'mediaDevices', 'MediaRecorder', 'SpeechRecognition', 'speechSynthesis', 'AudioContext', 'Audio', 'Worker', 'setTimeout', 'setInterval', 'addEventListener']);
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

function chatMarkup(port, overrides = {}) {
  const chat = { draft: '', pending: false, feedback: '', setDraft() {}, submit() {}, cancel() {}, ...overrides };
  return renderToStaticMarkup(createElement(app.ChatWorkspace, { chatPort: port, chat }));
}
function connected(messages = [], sendText = async () => {}) {
  return { status: 'connected', messages, sendText };
}

test('default chat is an editable local draft with an honest empty transcript', () => {
  const markup = renderToStaticMarkup(createElement(app.default));
  assert.match(markup, /<h3 id="conversation-heading">Conversation<\/h3>/);
  assert.match(markup, /role="region" aria-label="Conversation transcript" tabindex="0"/);
  assert.match(markup, /<label for="chat-draft">Local draft only<\/label>/);
  const textarea = markup.match(/<textarea\b([^>]*)>/)[1];
  assert.doesNotMatch(textarea, /disabled|readonly|autofocus/);
  assert.match(textarea, /aria-describedby="draft-help draft-count send-reason"/);
  assert.match(markup, /0 \/ 4000 characters/);
  assert.match(markup, /Nothing is stored or sent automatically/);
  assert.doesNotMatch(markup, /<li class="message|<time\b/);
});

test('voice remains unavailable with a disabled explained microphone and text fallback', () => {
  const markup = chatMarkup();
  assert.match(markup, /<h3 id="voice-heading">Voice unavailable<\/h3>/);
  assert.match(markup, /<button[^>]*disabled=""[^>]*aria-describedby="voice-reason">Listen with microphone/);
  assert.match(markup, /Audio capture, wake detection, STT and TTS integration are deferred/);
  assert.match(markup, /Text entry remains available without enabling a microphone/);
  assert.doesNotMatch(markup, />Listening<|>Speaking<|>Ready<|voice ready|voice listening/i);
});

test('draft admission trims text and enforces the exact raw 4000-character limit', () => {
  for (const value of ['', ' ', '\n\t\r', null, undefined, 0, {}, 'x'.repeat(4001), ' '.repeat(4000) + 'x']) {
    assert.equal(app.validateDraft(value), undefined);
  }
  assert.equal(app.validateDraft('  hello\n'), 'hello');
  assert.equal(app.validateDraft('x'.repeat(4000)), 'x'.repeat(4000));
  assert.equal(app.validateDraft(' '.repeat(3999) + 'x'), 'x');
  const markup = chatMarkup(connected(), { draft: 'x'.repeat(4000) });
  assert.match(markup, /4000 \/ 4000 characters — character limit reached/);
  assert.doesNotMatch(markup.match(/<button class="send-button"([^>]*)>/)[1], /disabled/);
});

test('disabled send admits only connected, valid and non-pending drafts', () => {
  for (const port of [undefined, null, {}, { status: 'connected', messages: [] }, { status: 'connected', messages: {}, sendText() {} }, { ...connected(), status: 'unavailable' }, { ...connected(), status: 'degraded' }]) {
    assert.match(chatMarkup(port, { draft: 'hello' }), /<button class="send-button"[^>]*disabled=""/);
  }
  for (const draft of ['', ' \n', 'x'.repeat(4001)]) {
    assert.match(chatMarkup(connected(), { draft }), /<button class="send-button"[^>]*disabled=""/);
  }
  const pending = chatMarkup(connected(), { draft: 'hello', pending: true });
  assert.match(pending, /aria-busy="true"/);
  assert.match(pending, /<textarea[^>]*readOnly=""/);
  assert.match(pending, /disabled=""[^>]*>Sending…/);
  assert.match(pending, /Cancel waiting/);
});

test('borrowed snapshot renders user and assistant content as literal escaped text', () => {
  const content = '<script>run()</script> [link](https://example.invalid) ![image](x)\n**literal**';
  const markup = chatMarkup(connected([
    { id: 'opaque-1', role: 'user', content },
    { id: 'opaque-2', role: 'assistant', content: 'permission granted: still untrusted text' },
  ]));
  assert.match(markup, /<h4>User<\/h4>/);
  assert.match(markup, /<h4>Assistant<\/h4>/);
  assert.match(markup, /&lt;script&gt;run\(\)&lt;\/script&gt;/);
  assert.ok(markup.includes('[link](https://example.invalid) ![image](x)'));
  assert.match(markup, /permission granted: still untrusted text/);
  assert.doesNotMatch(markup, /<script|<img|<a\b|<strong>|<code|<time/);
});

test('transcript rejects malformed roles, fields, IDs, duplicate IDs and oversize entries', () => {
  const valid = { id: 'one', role: 'user', content: 'safe' };
  const values = [valid, { ...valid }, null, {}, { ...valid, role: 'system' }, { ...valid, role: 'developer' },
    { ...valid, id: ' ' }, { ...valid, id: 'x'.repeat(129) }, { ...valid, content: 3 },
    { ...valid, content: ' \n' }, { ...valid, content: 'x'.repeat(4001) }, { ...valid, approved: true }];
  const snapshot = app.boundedMessages(values);
  assert.equal(snapshot.messages.length, 1);
  assert.equal(snapshot.messages[0].content, 'safe');
  assert.equal(snapshot.omitted, true);
  assert.equal(app.boundedMessages({}).messages.length, 0);
  const many = Array.from({ length: 10000 }, (_, id) => ({ id: String(id), role: 'assistant', content: 'x'.repeat(4000) }));
  assert.equal(app.boundedMessages(many).messages.length, 64);
  assert.equal(app.boundedMessages(many).omitted, true);
  assert.match(chatMarkup(connected(values)), /No message is partially clipped/);
  assert.doesNotMatch(chatMarkup(connected(values)), /<h4>System|<h4>Developer/);
});

test('malformed accessors are never evaluated or coerced during adapter/message checks', () => {
  let reads = 0;
  const accessor = { get status() { reads += 1; throw new Error('private'); }, messages: [], sendText() {} };
  assert.equal(app.readChatPort(accessor), undefined);
  const entry = { id: 'one', role: 'user', get content() { reads += 1; throw new Error('private'); } };
  assert.equal(app.boundedMessages([entry]).messages.length, 0);
  const proxy = new Proxy({}, { getOwnPropertyDescriptor() { throw new Error('private'); } });
  assert.equal(app.readChatPort(proxy), undefined);
  const badList = new Proxy([], { get() { throw new Error('private'); } });
  assert.equal(app.boundedMessages(badList).omitted, true);
  const hiddenExtra = { id: 'hidden', role: 'user', content: 'text' };
  Object.defineProperty(hiddenExtra, 'approved', { value: true });
  assert.equal(app.boundedMessages([hiddenExtra]).messages.length, 0);
  const entries = [];
  Object.defineProperty(entries, '0', { get() { reads += 1; throw new Error('private'); } });
  assert.equal(app.boundedMessages(entries).messages.length, 0);
  assert.equal(app.validateDraft({ toString() { reads += 1; throw new Error('private'); } }), undefined);
  assert.equal(reads, 0);
});

test('no callback runs without explicit connected admission or valid text', async () => {
  let calls = 0;
  const port = connected([], async () => { calls += 1; });
  for (const value of [undefined, {}, { ...port, status: 'unavailable' }, { ...port, status: 'degraded' }]) {
    assert.equal(await app.sendDraft(value, 'hi', { pending: false, cancelled: false }), 'unavailable');
  }
  for (const draft of ['', ' \n', 'x'.repeat(4001), {}]) {
    assert.equal(await app.sendDraft(port, draft, { pending: false, cancelled: false }), 'invalid');
  }
  renderToStaticMarkup(createElement(app.default, { chatPort: port }));
  assert.equal(calls, 0, 'render must not auto-submit');
});

test('one-flight send waits for explicit acknowledgement and never manufactures history', async () => {
  let acknowledge;
  const inputs = [];
  const messages = [];
  const port = connected(messages, (text) => { inputs.push(text); return new Promise((resolve) => { acknowledge = resolve; }); });
  const flight = { pending: false, cancelled: false };
  const request = app.sendDraft(port, '  hello \n', flight);
  assert.equal(flight.pending, true);
  assert.equal(await app.sendDraft(port, 'duplicate', flight), 'busy');
  assert.deepEqual(inputs, ['hello']);
  assert.equal(app.acknowledgedDraft('  hello \n', '  hello \n', 'busy'), '  hello \n');
  assert.equal(messages.length, 0);
  acknowledge();
  const outcome = await request;
  assert.equal(outcome, 'acknowledged');
  assert.equal(flight.pending, false);
  assert.equal(app.acknowledgedDraft('  hello \n', '  hello \n', outcome), '');
  assert.equal(app.acknowledgedDraft('new draft', '  hello \n', outcome), 'new draft');
  assert.equal(messages.length, 0);
});

test('failure, cancellation and malformed acknowledgements retain the draft', async () => {
  for (const sendText of [() => { throw new Error('private token'); }, async () => { throw new Error('private native path'); }, () => undefined,
    () => ({ then() { throw new Error('arbitrary thenable must not run'); } }), async () => 'false acknowledgement']) {
    const flight = { pending: false, cancelled: false };
    const outcome = await app.sendDraft(connected([], sendText), 'keep me', flight);
    assert.equal(outcome, 'failed');
    assert.equal(app.acknowledgedDraft('keep me', 'keep me', outcome), 'keep me');
    assert.equal(flight.pending, false);
  }
  let acknowledge;
  const flight = { pending: false, cancelled: false };
  const request = app.sendDraft(connected([], () => new Promise((resolve) => { acknowledge = resolve; })), 'keep me', flight);
  app.cancelWait(flight);
  assert.equal(flight.pending, true, 'local cancellation cannot authorize duplicate delivery');
  assert.equal(await app.sendDraft(connected(), 'duplicate', flight), 'busy');
  acknowledge();
  assert.equal(await request, 'cancelled');
  assert.equal(flight.pending, false);
  assert.equal(app.acknowledgedDraft('keep me', 'keep me', 'cancelled'), 'keep me');
});

// This deterministic hook harness tests state/handlers, not native DOM event delivery.
function hookHarness(port) {
  const state = [];
  const refs = [];
  let stateIndex = 0;
  let refIndex = 0;
  const module = loadApp({ ...React,
    useState(initial) {
      const index = stateIndex++;
      if (!(index in state)) state[index] = initial;
      return [state[index], (value) => { state[index] = typeof value === 'function' ? value(state[index]) : value; }];
    },
    useRef(initial) { const index = refIndex++; return refs[index] ??= { current: initial }; },
    useEffect() {},
  });
  return () => {
    stateIndex = 0;
    refIndex = 0;
    const shell = module.default({ chatPort: port });
    const chat = shell.props.children[2].props.children[1].props.children.props.chat;
    const buttons = shell.props.children[1].props.children[1].props.children.flatMap((group) => group.props.children[1].props.children.map((item) => item.props.children));
    return { ...chat, navigate(label) { buttons.find((button) => button.props.children === label).props.onClick(); } };
  };
}

test('actual composer handlers retain draft while pending/failing and clear only after ack', async () => {
  let acknowledge;
  const inputs = [];
  const read = hookHarness(connected([], (text) => { inputs.push(text); return new Promise((resolve) => { acknowledge = resolve; }); }));
  read().setDraft('  actual handler  ');
  let prevented = 0;
  const event = { preventDefault() { prevented += 1; } };
  const request = read().submit(event);
  assert.equal(read().pending, true);
  assert.equal(read().draft, '  actual handler  ');
  read().navigate('Media');
  assert.equal(read().pending, true);
  read().navigate('Home / Chat');
  assert.equal(read().draft, '  actual handler  ');
  await read().submit(event);
  assert.deepEqual(inputs, ['actual handler']);
  acknowledge();
  await request;
  assert.equal(read().draft, '');
  assert.equal(read().pending, false);
  assert.match(read().feedback, /Adapter acknowledged/);
  assert.equal(prevented, 2);
  const failed = hookHarness(connected([], async () => { throw new Error('private token native path'); }));
  failed().setDraft('retain');
  await failed().submit(event);
  assert.equal(failed().draft, 'retain');
  assert.match(failed().feedback, /check the connection before retrying/);
  assert.doesNotMatch(failed().feedback, /private|token|native path/);
});

test('actual cancel handler preserves draft even after a late acknowledgement', async () => {
  let acknowledge;
  const read = hookHarness(connected([], () => new Promise((resolve) => { acknowledge = resolve; })));
  read().setDraft('retain after cancellation');
  const request = read().submit({ preventDefault() {} });
  read().cancel();
  assert.equal(read().pending, true);
  assert.match(read().feedback, /does not cancel delivery/);
  acknowledge();
  await request;
  assert.equal(read().draft, 'retain after cancellation');
  assert.equal(read().pending, false);
  assert.match(read().feedback, /Delivery outcome is unknown/);
});

test('composer keeps native form semantics, local state ownership and useful polite feedback', () => {
  assert.match(appSource, /const chat = useChatDraft\(chatPort\)/);
  assert.match(appSource, /onSubmit=\{chat.submit\}/);
  assert.match(appSource, /event.preventDefault\(\)/);
  assert.match(appSource, /setDraft\(\(current\) => acknowledgedDraft\(current, submitted, outcome\)\)/);
  assert.match(appSource, /role="status" aria-live="polite" aria-atomic="true"/);
  assert.doesNotMatch(appSource, /autoFocus|onKeyDown|onKeyUp|Date\.|toLocaleString/);
});

test('chat CSS bounds transcript scrolling, wraps literal content and keeps the composer reachable', () => {
  assert.match(css, /\.transcript\s*\{[^}]*max-height:[^}]*overflow-y:\s*auto/);
  assert.match(css, /\.message-content\s*\{[^}]*white-space:\s*pre-wrap;[^}]*overflow-wrap:\s*anywhere/);
  assert.match(css, /\.composer textarea\s*\{[^}]*width:\s*100%;[^}]*resize:\s*vertical/);
  assert.match(css, /@media \(max-width: 1000px\)[\s\S]*?\.conversation-layout \{ grid-template-columns: minmax\(0, 1fr\)/);
  assert.doesNotMatch(css, /text-overflow:\s*ellipsis|line-clamp|\.composer[^}]*position:\s*(absolute|fixed)/);
});
