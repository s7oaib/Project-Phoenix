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
  for (const [id, label] of expectedViews.filter(([id]) => id !== 'home' && id !== 'agents')) {
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
  for (const name of ['Object.freeze', 'canonicalAgents.map', 'agentRoster.find', 'agentRoster.filter', 'boundedAgentQuery', 'value.slice', 'boundedAgentQuery(value).toLowerCase', 'agent.alias.toLowerCase', 'agent.alias.toLowerCase().includes', 'agent.internalId.toLowerCase', 'agent.internalId.toLowerCase().includes', 'agent.professionalRole.toLowerCase', 'agent.professionalRole.toLowerCase().includes', 'required.includes', 'optional.includes', 'required.every', 'keys.includes', 'agentDataFields', 'Number.isInteger', 'findAgent', 'agents.some', 'agents.push', 'setQuery', 'setSelectedId', 'filterAgents', 'readAgentSnapshot', 'snapshot.agents.find', 'matches.some', 'directory.search', 'matches.map', 'directory.select', 'useAgentDirectory']) allowedCalls.add(name);
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
function hookHarness(port, agentPort) {
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
    const shell = module.default({ chatPort: port, agentPort });
    const chat = shell.props.children[2].props.children[1].props.children.props.chat;
    const panel = shell.props.children[2].props.children[1].props.children;
    const buttons = shell.props.children[1].props.children[1].props.children.flatMap((group) => group.props.children[1].props.children.map((item) => item.props.children));
    return { ...chat, directory: panel.props.directory, panel, navigate(label) { buttons.find((button) => button.props.children === label).props.onClick(); } };
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

const rosterDocument = readFileSync(new URL('../docs/03_AGENT_ROSTER.md', import.meta.url), 'utf8');
const registrySource = readFileSync(new URL('../phoenix/agents/registry.py', import.meta.url), 'utf8');
const rosterTriples = rosterDocument.split(/\r?\n/).filter((line) => line.startsWith('| ') && !line.startsWith('| Internal ID '))
  .map((line) => line.split('|').slice(1, -1).map((cell) => cell.trim()));
function agentMarkup(agentPort, overrides = {}) {
  const directory = { query: '', selectedId: 'phoenix', search() {}, select() {}, ...overrides };
  return renderToStaticMarkup(createElement(app.AgentNetwork, { agentPort, directory }));
}
function agentPort(agents = [], status = 'connected') { return { status, agents }; }
const statusRecord = { internalId: 'phoenix', state: 'working', currentTask: 'Supplied task', provider: 'Supplied provider' };

test('all thirty display triples match both the documented roster and Python canonical tuple in exact order', () => {
  const pythonTuple = registrySource.split('DEFAULT_AGENT_DEFINITIONS:')[1].split('def build_default_agent_registry')[0];
  const pythonTriples = [...pythonTuple.matchAll(/\("([^"\n]+)", "([^"\n]+)", "([^"\n]+)"\)/g)].map((match) => match.slice(1));
  const actual = Array.from(app.agentRoster, (agent) => [agent.internalId, agent.alias, agent.professionalRole]);
  assert.equal(rosterTriples.length, 30);
  assert.deepEqual(pythonTriples, rosterTriples);
  assert.deepEqual(actual, rosterTriples);
  assert.equal(new Set(actual.map(([id]) => id)).size, 30);
  assert.deepEqual(actual.filter(([, , role]) => role === 'Supreme orchestrator'), [rosterTriples[0]]);
  assert.equal(actual.filter(([id]) => id !== 'phoenix').length, 29);
  assert.equal(actual.at(-1)[0], 'verifier');
  assert.ok(Object.isFrozen(app.agentRoster));
  assert.ok(app.agentRoster.every(Object.isFrozen));
});

test('directory identity lookup admits canonical IDs only and never coerces aliases or objects', () => {
  for (const [id, alias, role] of rosterTriples) {
    assert.equal(app.findAgent(id).professionalRole, role);
    assert.equal(app.findAgent(alias), undefined);
  }
  for (const value of ['PHOENIX', 'phoenix ', 'Phoenix', '__proto__', 'unknown', null, undefined, 2,
    { toString() { assert.fail('selection must not coerce'); } }]) assert.equal(app.findAgent(value), undefined);
});

test('default Agent Network shows thirty definitions with PHOENIX selected and no fabricated runtime', () => {
  const markup = agentMarkup();
  assert.match(markup, /<h3 id="agent-network-heading">Agent Network<\/h3>/);
  assert.match(markup, /30 defined logical roles: PHOENIX and 29 specialists/);
  assert.match(markup, /Definitions do not mean agents are initialized or running/);
  assert.match(markup, /Live agent state not connected/);
  assert.match(markup, /30 of 30 defined roles match/);
  const cards = [...markup.matchAll(/<button class="agent-card"([^>]*)>[\s\S]*?<span class="agent-id">([^<]+)<\/span><\/button>/g)];
  assert.deepEqual(cards.map((card) => card[2]), rosterTriples.map(([id]) => id));
  assert.equal(cards.filter((card) => card[1].includes('aria-pressed="true"')).length, 1);
  assert.match(cards[0][1], /aria-pressed="true"/);
  assert.match(markup, /PHOENIX is the sole supreme orchestrator/);
  assert.match(markup, /Status not reported/);
  assert.doesNotMatch(markup, /agents online|agents active|state: idle|state: working|Supplied current task:|Supplied model\/provider:|<time|<progress/i);
});

test('local search matches alias, internal ID and role case-insensitively without changing canonical order', () => {
  for (const [query, ids] of [['aIzEn', ['strategist']], ['MISSION_COORDINATOR', ['mission_coordinator']],
    ['VERIFICATION/qa', ['verifier']], ['monitoring', ['system_intelligence', 'silent_monitor']], ['Phoenix', ['phoenix']]]) {
    assert.deepEqual(Array.from(app.filterAgents(query), (agent) => agent.internalId), ids);
    const markup = agentMarkup(undefined, { query });
    assert.ok(markup.includes(`${ids.length} of 30 defined roles match`));
  }
  assert.deepEqual(Array.from(app.filterAgents(''), (agent) => agent.internalId), rosterTriples.map(([id]) => id));
});

test('search is strictly string bounded at eighty UTF-16 units with safe Unicode and no-results feedback', () => {
  assert.equal(app.AGENT_QUERY_LIMIT, 80);
  assert.equal(app.boundedAgentQuery('x'.repeat(81)), 'x'.repeat(80));
  assert.equal(app.boundedAgentQuery('😀'.repeat(41)).length, 80);
  for (const value of [null, undefined, {}, 42, { toString() { assert.fail('no coercion'); } }]) assert.equal(app.boundedAgentQuery(value), '');
  for (const query of ['😀', '\uD800', '<script>throw 1</script>', '__proto__', 'x'.repeat(80)]) {
    assert.equal(app.filterAgents(query).length, 0);
    const markup = agentMarkup(undefined, { query });
    assert.match(markup, /0 of 30 defined roles match/);
    assert.match(markup, /No defined agents match. Clear search/);
    assert.doesNotMatch(markup, /<script|<iframe|<img/);
  }
});

test('search and directory controls use named native inputs/buttons and explicit selected semantics', () => {
  const markup = agentMarkup();
  assert.match(markup, /<label for="agent-query">Search defined agents<\/label>/);
  assert.match(markup, /<input id="agent-query" type="search" maxLength="80"[^>]*aria-describedby="agent-search-help agent-match-count"/);
  assert.match(markup, /id="agent-match-count" role="status" aria-live="polite" aria-atomic="true"/);
  assert.match(markup, /<section class="agent-results" aria-label="Matching defined agents">/);
  assert.equal([...markup.matchAll(/<button\b/g)].length, 31, 'thirty selection controls plus Clear search only');
  for (const [, attrs] of markup.matchAll(/<button class="agent-card"([^>]*)>/g)) {
    assert.match(attrs, /type="button"/);
    assert.match(attrs, /aria-pressed="(?:true|false)"/);
    assert.match(attrs, /aria-controls="agent-detail"/);
    assert.doesNotMatch(attrs, /disabled|tabindex|role=/);
  }
  assert.doesNotMatch(markup, /Activate|Dispatch|Reassign|Approve|Deny|Start agent|Stop agent/);
});

test('selected details display exact identity and preserve a clearly labelled off-list selection', () => {
  for (const [id, alias, role] of rosterTriples) {
    const markup = agentMarkup(undefined, { selectedId: id });
    const detail = markup.split('id="agent-detail"')[1];
    assert.ok(detail.includes(`<dd>${alias}</dd>`));
    assert.ok(detail.includes(`<dd>${id}</dd>`));
    assert.ok(detail.includes(`<dd>${role}</dd>`));
    assert.ok(detail.includes(id === 'phoenix' ? 'sole supreme orchestrator' : 'Specialist role subordinate to PHOENIX'));
  }
  const filtered = agentMarkup(undefined, { selectedId: 'verifier', query: 'AIZEN' });
  assert.match(filtered, /Selected: LEVI — outside the current search results/);
  assert.match(filtered, /Clear search to show its selection button/);
  assert.equal([...filtered.matchAll(/aria-pressed="true"/g)].length, 0);
  for (const selectedId of ['AIZEN', 'PHOENIX', 'unknown', null, {}]) {
    const markup = agentMarkup(undefined, { selectedId });
    assert.match(markup, /No canonical agent selected/);
    assert.doesNotMatch(markup.split('id="agent-detail"')[1], /Selected: PHOENIX|sole supreme orchestrator/);
  }
});

test('actual directory search/select/clear handlers and navigation retain selection independently of chat', () => {
  const read = hookHarness();
  assert.equal(read().directory.selectedId, 'phoenix');
  read().setDraft('retain existing chat');
  read().navigate('Agent Network');
  let network = app.AgentNetwork({ directory: read().directory });
  const searchArea = network.props.children[1];
  searchArea.props.children[2].props.children[0].props.onChange({ currentTarget: { value: 'AIZEN' } });
  assert.equal(read().directory.query, 'AIZEN');
  network = app.AgentNetwork({ directory: read().directory });
  const list = network.props.children[2].props.children[0].props.children;
  list.props.children[0].props.children.props.onClick();
  assert.equal(read().directory.selectedId, 'strategist');
  for (const invalid of ['PHOENIX', 'AIZEN', 'unknown', {}, undefined]) read().directory.select(invalid);
  assert.equal(read().directory.selectedId, 'strategist', 'unknown selection keeps the last valid identity');
  read().navigate('Home / Chat');
  assert.equal(read().draft, 'retain existing chat');
  read().navigate('Agent Network');
  assert.equal(read().directory.selectedId, 'strategist');
  assert.equal(read().directory.query, 'AIZEN');
  network = app.AgentNetwork({ directory: read().directory });
  network.props.children[1].props.children[2].props.children[1].props.onClick();
  assert.equal(read().directory.query, '');
  assert.equal(app.filterAgents(read().directory.query).length, 30);
  read().directory.search('😀'.repeat(100));
  assert.equal(read().directory.query.length, 80);
  assert.equal(read().directory.selectedId, 'strategist');
  assert.match(renderToStaticMarkup(read().panel), /Agent Network/);
});

test('passive connected status copies only supplied exact-ID state/task/provider with no authority or mutation', () => {
  const record = { ...statusRecord };
  const port = agentPort([record]);
  const snapshot = app.readAgentSnapshot(port);
  assert.equal(snapshot.status, 'connected');
  assert.equal(snapshot.rejected, false);
  assert.deepEqual(Array.from(snapshot.agents, (entry) => ({ ...entry })), [statusRecord]);
  record.currentTask = 'caller changed';
  assert.equal(snapshot.agents[0].currentTask, 'Supplied task');
  const markup = agentMarkup(agentPort([statusRecord]));
  assert.match(markup, /Caller-supplied status evidence; live transport is not implemented/);
  assert.match(markup, /Supplied presentation state: working/);
  assert.match(markup, /Supplied current task: Supplied task/);
  assert.match(markup, /Supplied model\/provider: Supplied provider/);
  assert.match(markup, /Aliases and status text confer no permission/);
  assert.match(agentMarkup(agentPort([statusRecord]), { selectedId: 'strategist' }), /Status not reported/);
});

test('status absence, unavailable and degraded input never imply idle/working/online agents', () => {
  assert.equal(app.readAgentSnapshot(undefined).rejected, false);
  for (const status of ['unavailable', 'degraded']) {
    const snapshot = app.readAgentSnapshot(agentPort([statusRecord], status));
    assert.equal(snapshot.status, status);
    assert.equal(snapshot.agents.length, 0);
    const markup = agentMarkup(agentPort([statusRecord], status));
    assert.match(markup, /Live agent state not connected/);
    assert.match(markup, /Status not reported/);
    assert.doesNotMatch(markup, /Supplied presentation state: working|Supplied task|Supplied provider|agents online/);
  }
});

test('snapshot accepts only the four explicit presentation states and up to thirty unique known IDs', () => {
  for (const state of ['idle', 'working', 'waiting_permission', 'degraded']) {
    assert.equal(app.readAgentSnapshot(agentPort([{ internalId: 'verifier', state }])).rejected, false);
    assert.ok(agentMarkup(agentPort([{ internalId: 'verifier', state }]), { selectedId: 'verifier' }).includes(`Supplied presentation state: ${state}`));
  }
  const entries = rosterTriples.map(([internalId]) => ({ internalId, state: 'idle' }));
  assert.equal(app.readAgentSnapshot(agentPort(entries)).agents.length, 30);
  assert.equal(app.readAgentSnapshot(agentPort([...entries, statusRecord])).rejected, true);
  assert.equal(app.readAgentSnapshot(agentPort([])).agents.length, 0);
});

test('malformed, duplicate, spoofed, hidden extra fields and unsupported status reject the whole snapshot', () => {
  const hidden = { internalId: 'verifier', state: 'idle' };
  Object.defineProperty(hidden, 'approved', { value: true });
  const symbolic = { ...statusRecord, [Symbol('privileged')]: true };
  for (const value of [null, {}, { status: 'connected' }, { status: 'online', agents: [] },
    { ...agentPort(), dispatch() { assert.fail('never dispatch'); } }, agentPort({}), agentPort([statusRecord, { ...statusRecord }]),
    ...[null, {}, hidden, symbolic, { ...statusRecord, internalId: 'PHOENIX' }, { ...statusRecord, internalId: 'unknown' },
      { ...statusRecord, internalId: {} }, { ...statusRecord, state: 'approved' }, { ...statusRecord, state: true },
      { ...statusRecord, approved: true }, { ...statusRecord, alias: 'PHOENIX' }, { ...statusRecord, currentTask: undefined },
      { ...statusRecord, provider: null }, Object.create(statusRecord)].map((entry) => agentPort([{ internalId: 'verifier', state: 'idle' }, entry]))]) {
    const snapshot = app.readAgentSnapshot(value);
    assert.equal(snapshot.rejected, true);
    assert.equal(snapshot.agents.length, 0, 'no partial state after malformed admission');
    const markup = agentMarkup(value);
    assert.match(markup, /Supplied status snapshot was rejected/);
    assert.doesNotMatch(markup, /Supplied task|Supplied provider|Supplied presentation state:/);
  }
});

test('task/provider bounds count UTF-16 units and reject rather than clip oversized security text', () => {
  for (const [field, limit] of [['currentTask', 240], ['provider', 120]]) {
    for (const text of ['', 'x'.repeat(limit), '😀'.repeat(limit / 2)]) {
      assert.equal(app.readAgentSnapshot(agentPort([{ ...statusRecord, [field]: text }])).rejected, false);
    }
    for (const text of ['x'.repeat(limit + 1), '😀'.repeat(limit / 2 + 1), {}, 1]) {
      assert.equal(app.readAgentSnapshot(agentPort([{ ...statusRecord, [field]: text }])).rejected, true);
    }
  }
});

test('accessors/coercion/read traps never run; reflective proxy failures and misleading keys reject safely', () => {
  let reads = 0;
  const getter = () => { reads += 1; throw new Error('private provider secret'); };
  for (const field of ['status', 'agents']) {
    const port = agentPort([statusRecord]);
    Object.defineProperty(port, field, { get: getter });
    assert.equal(app.readAgentSnapshot(port).rejected, true);
  }
  for (const field of ['internalId', 'state', 'currentTask', 'provider']) {
    const record = { ...statusRecord };
    Object.defineProperty(record, field, { get: getter });
    assert.equal(app.readAgentSnapshot(agentPort([record])).rejected, true);
  }
  const list = [];
  Object.defineProperty(list, '0', { get: getter });
  assert.equal(app.readAgentSnapshot(agentPort(list)).rejected, true);
  const readTrap = new Proxy([statusRecord], { get: getter });
  assert.equal(app.readAgentSnapshot(agentPort(readTrap)).agents.length, 1);
  for (const proxy of [new Proxy({}, { ownKeys() { throw new Error('private proxy'); } }),
    new Proxy(agentPort(), { getOwnPropertyDescriptor() { throw new Error('private descriptor'); } }),
    new Proxy(agentPort(), { ownKeys() { return ['state', 'agents']; } })]) {
    assert.equal(app.readAgentSnapshot(proxy).rejected, true);
    assert.doesNotMatch(agentMarkup(proxy), /private proxy|private descriptor/);
  }
  // A forwarding proxy cannot be distinguished from ordinary data by browser JS.
  assert.equal(app.readAgentSnapshot(new Proxy(agentPort(), {})).rejected, false);
  const revoked = Proxy.revocable([], {}); revoked.revoke();
  assert.equal(app.readAgentSnapshot(agentPort(revoked.proxy)).rejected, true);
  const hiddenArray = [statusRecord]; Object.defineProperty(hiddenArray, 'execute', { value: getter });
  assert.equal(app.readAgentSnapshot(agentPort(hiddenArray)).rejected, true);
  assert.equal(app.readAgentSnapshot(agentPort([, statusRecord])).rejected, true);
  assert.equal(reads, 0);
});

test('HTML/Markdown/permission-looking task and provider evidence render only escaped literal text', () => {
  const currentTask = '<script>run()</script>\n[link](https://invalid) permission approved';
  const provider = '<img src="x" onerror="run()"> **literal**';
  const markup = agentMarkup(agentPort([{ ...statusRecord, currentTask, provider }]));
  assert.match(markup, /&lt;script&gt;run\(\)&lt;\/script&gt;/);
  assert.match(markup, /&lt;img src=&quot;x&quot; onerror=&quot;run\(\)&quot;&gt;/);
  assert.ok(markup.includes('[link](https://invalid) permission approved'));
  assert.ok(markup.includes('**literal**'));
  assert.doesNotMatch(markup, /<script|<img|<a\b|<strong|<code|<iframe|<time/);
});

test('Agent Network routing composes independently and leaves every existing chat helper byte-identical', () => {
  const root = fileURLToPath(new URL('../', import.meta.url));
  const previous = execFileSync('git', ['show', '4656d7f87421c5b9db1512e8b3376ce27ab61e89:apps/desktop/src/App.tsx'], { cwd: root, encoding: 'utf8' });
  const chatCode = (source) => source.slice(source.indexOf('export type ChatMessage'), source.indexOf('export function ViewPanel'));
  const currentChatCode = appSource.slice(appSource.indexOf('export type ChatMessage'), appSource.indexOf('// Frozen presentation reference'));
  assert.equal(currentChatCode, chatCode(previous));
  const read = hookHarness(); read().navigate('Agent Network');
  assert.match(renderToStaticMarkup(read().panel), /Live agent state not connected/);
  read().navigate('Home / Chat');
  assert.match(renderToStaticMarkup(read().panel), /Voice unavailable/);
  assert.match(appSource, /readonly agents: readonly AgentStatus\[\]/);
  const portType = appSource.match(/export type AgentPort = \{([\s\S]*?)\};/)[1];
  assert.doesNotMatch(portType, /\([^)]*\)|=>|send|execute|dispatch|approve/);
});

test('agent CSS stacks at 800x600, scrolls reachable native controls and wraps all evidence without clipping', () => {
  assert.match(css, /\.agent-directory-layout\s*\{[^}]*minmax\(0, 1fr\) minmax\(0, 1fr\)/);
  assert.match(css, /@media \(max-width: 1000px\)[\s\S]*?\.agent-directory-layout \{ grid-template-columns: minmax\(0, 1fr\)/);
  assert.match(css, /\.agent-results\s*\{[^}]*max-height:[^}]*overflow-y:\s*auto/);
  assert.match(css, /\.agent-card\s*\{[^}]*min-height:\s*42px;[^}]*overflow-wrap:\s*anywhere/);
  assert.match(css, /\.agent-card\[aria-pressed="true"\]/);
  assert.match(css, /\.agent-evidence\s*\{[^}]*white-space:\s*pre-wrap;[^}]*overflow-wrap:\s*anywhere/);
  assert.match(css, /@media \(max-width: 460px\)[\s\S]*?\.agent-search-controls \{ flex-direction: column/);
  assert.doesNotMatch(css, /\.agent-(?:detail|search|results)[^}]*position:\s*(?:fixed|absolute)|text-overflow:\s*ellipsis|line-clamp/);
});
