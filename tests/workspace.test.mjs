import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
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
  for (const [id, label] of expectedViews.filter(([id]) => id !== 'home' && id !== 'agents' && id !== 'tasks' && id !== 'permissions' && id !== 'memory' && id !== 'files' && id !== 'models' && id !== 'voice' && id !== 'settings' && id !== 'observability')) {
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
  for (const name of ['taskText', 'taskArray', 'taskTopology', 'readTaskGraphPort', 'useTaskGraph', 'result.push', 'steps.map', 'steps.filter', 'steps.filter((step) => step.dependsOn.length === 0).map', 'steps.findIndex', 'Math.max', 'step.dependsOn.map', 'ordered.findIndex', 'ordered.push', 'layers.push', 'steps.forEach', 'item.dependsOn.includes', 'taskStates.some', 'steps.some', 'Number.isFinite', 'dependsOn.includes', 'dependsOn.push', 'inputRefs.push', 'steps.push', 'step.dependsOn.some', 'taskStates.find', 'graph?.ordered.find', 'setTaskSelection', 'graph.roots.join', 'graph.ordered.map', 'task.select', 'step.dependsOn.join', 'selected.dependsOn.join', 'selected.inputRefs.map']) allowedCalls.add(name);
  for (const name of ['permissionId', 'permissionUtc', 'value.match', 'Number', 'readPermissionRequest', 'readPermissionEvaluation', 'permissionEvidenceRules.find', 'rule.risks.some', 'readPermissionPort', 'requests.some', 'requests.push', 'permissionNeedsIntent', 'prunePermissionIntents', 'flight.submittedIds.filter', 'snapshot.requests.find', 'port.requests.find', 'flight.submittedIds.includes', 'port.submitDecision', 'flight.submittedIds.push', 'filterPermissionRequests', 'entries.filter', 'text.some', 'field.toLowerCase', 'field.toLowerCase().includes', 'usePermissionCenter', 'setPermissionQuery', 'setSelectedPermissionId', 'deliverPermissionIntent', 'setPermissionFeedbackId', 'setPermissionPending', 'setPermissionFeedback', 'flight.current.submittedIds.includes', 'permissions.search', 'permissions.select', 'permissions.submit']) allowedCalls.add(name);
  for (const name of ['normalizedEvidenceText', 'memoryClasses.some', 'fileKinds.some', 'evidenceUtcKey', 'fraction.padEnd', 'readMemoryRecord', 'readFileRecord', 'Number.isSafeInteger', 'readEvidencePort', 'entries.some', 'entries.push', 'readMemoryPort', 'readFilePort', 'Object.values', 'Object.values(entry).some', '`${field}`.includes', 'snapshot.entries.find', 'setEvidenceQuery', 'setEvidenceId', 'filterEvidence', 'browser.search', 'memory.select', 'files.select', 'useEvidenceBrowser']) allowedCalls.add(name);
  for (const name of ['runtimeStatus', 'runtimeKey', 'providerStatuses.some', 'voiceStates.some', 'voiceComponentIds.some', 'readModelPort', 'readVoicePort', 'filterRuntime', "(field ? 'true' : 'false').includes", 'entries.find', 'setRuntimeQuery', 'setRuntimeKey', 'useRuntimeBrowser', 'runtimeValue', 'models.select', 'voice.select']) allowedCalls.add(name);
  for (const name of ['diagnosticMetricNames.some', 'diagnosticSeverities.some', 'diagnosticTraceKinds.some', 'readDiagnosticEntry', 'readDiagnosticCollection', 'readObservabilityPort', 'filterDiagnostics', 'setDiagnosticQuery', 'setDiagnosticId', 'useDiagnosticBrowser', 'browser.select']) allowedCalls.add(name);
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
  // PHX-088 (ADR-075) intentionally changed the native host files; they stay pinned, at the PHX-088 commit.
  // PHX-103 (ADR-088) intentionally added websockets to pyproject.toml and uv.lock; they stay pinned, at the PHX-103 commit.
  // PHX-106/107 (ADR-091, ADR-092) intentionally changed the desktop manifest, lock, entry point, Tauri config and
  // native host (sidecar, proxy, ACL); they stay pinned, at the PHX-106/107 implementation commit.
  const baselines = {
    '4a70867203a70d3a85e2c141ef0dbb92bdff42e2': ['package.json', 'apps/desktop/tsconfig.json', 'apps/desktop/vite.config.ts'],
    'e34ca3d18e9444ff5ed32b3f1abb60fc5344f246': ['package-lock.json', 'apps/desktop/package.json', 'apps/desktop/src/main.tsx', 'apps/desktop/src-tauri/tauri.conf.json',
      'apps/desktop/src-tauri/Cargo.toml', 'apps/desktop/src-tauri/Cargo.lock', 'apps/desktop/src-tauri/src/main.rs', 'apps/desktop/src-tauri/build.rs'],
    '829670b7713eacf246e3dd282e0e15f1c94c142f': ['pyproject.toml', 'uv.lock'],
  };
  for (const [commit, paths] of Object.entries(baselines)) {
    for (const path of paths) {
      const actual = execFileSync('git', ['hash-object', '--path=' + path, path], { cwd: root, encoding: 'utf8' }).trim();
      const expected = execFileSync('git', ['rev-parse', commit + ':' + path], { cwd: root, encoding: 'utf8' }).trim();
      assert.equal(actual, expected, path);
    }
  }
});

test('desktop grants exactly the PHX-106 proxy commands and event listening, with an IPC-only production CSP', () => {
  const config = readJson('../apps/desktop/src-tauri/tauri.conf.json');
  // PHX-106 (ADR-091): one capability, main window only, exactly these four permissions.
  assert.deepEqual(config.app.security.capabilities, [{
    identifier: 'desktop-backend-proxy',
    description: "PHX-106/107: the two backend proxy commands and listening to the shell's backend events. No plugins, files, shell or HTTP.",
    windows: ['main'],
    permissions: ['allow-api-request', 'allow-backend-status', 'core:event:allow-listen', 'core:event:allow-unlisten'],
  }]);
  assert.equal(config.app.withGlobalTauri ?? false, false);
  assert.deepEqual(config.plugins ?? {}, {});
  assert.equal(existsSync(new URL('../apps/desktop/src-tauri/capabilities', import.meta.url)), false);
  // Only Tauri 2's IPC channels may be fetched: no backend, network or WebSocket origin.
  assert.equal(config.app.security.csp, "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-src 'none'; connect-src ipc: http://ipc.localhost");
  const devConnect = config.app.security.devCsp.split(';').map((directive) => directive.trim()).filter((directive) => directive.startsWith('connect-src'));
  assert.deepEqual(devConnect, ["connect-src 'self' ws://127.0.0.1:5173 ipc: http://ipc.localhost"]);
  assert.doesNotMatch(config.app.security.csp, /unsafe-inline|unsafe-eval|\*/);
  // The ACL commands, the registered handlers and the crate's dependencies match exactly; no Tauri plugin.
  const buildScript = readFileSync(new URL('../apps/desktop/src-tauri/build.rs', import.meta.url), 'utf8');
  assert.deepEqual([...buildScript.matchAll(/\.commands\(&\[([^\]]*)\]\)/g)].map((match) => match[1]), ['"api_request", "backend_status"']);
  const host = readFileSync(new URL('../apps/desktop/src-tauri/src/main.rs', import.meta.url), 'utf8');
  assert.deepEqual([...host.matchAll(/generate_handler!\[([^\]]*)\]/g)].map((match) => match[1].replace(/\s+/g, ' ').replace(/,?\s*$/, '').trim()), ['proxy::api_request, backend::backend_status']);
  const commands = ['backend.rs', 'proxy.rs', 'events.rs'].flatMap((file) => [...readFileSync(new URL(`../apps/desktop/src-tauri/src/${file}`, import.meta.url), 'utf8').matchAll(/#\[tauri::command\]\s*pub (?:async )?fn (\w+)/g)].map((match) => match[1]));
  assert.deepEqual(commands.sort(), ['api_request', 'backend_status']);
  const manifest = readFileSync(new URL('../apps/desktop/src-tauri/Cargo.toml', import.meta.url), 'utf8');
  assert.doesNotMatch(manifest, /tauri-plugin/);
  assert.match(manifest, /^tauri = \{ version = "2", features = \[\] \}$/m);
  assert.deepEqual(Object.keys(readJson('../apps/desktop/package.json').dependencies), ['@tauri-apps/api', 'react', 'react-dom']);
  assert.equal(readJson('../apps/desktop/package.json').dependencies['@tauri-apps/api'], '2.12.1');
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
function hookHarness(port, agentPort, taskPort, permissionPort, memoryPort, filePort, modelPort, voicePort, observabilityPort) {
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
    const shell = module.default({ chatPort: port, agentPort, taskPort, permissionPort, memoryPort, filePort, modelPort, voicePort, observabilityPort });
    const chat = shell.props.children[2].props.children[1].props.children.props.chat;
    const panel = shell.props.children[2].props.children[1].props.children;
    const buttons = shell.props.children[1].props.children[1].props.children.flatMap((group) => group.props.children[1].props.children.map((item) => item.props.children));
    return { ...chat, directory: panel.props.directory, task: panel.props.task, permissions: panel.props.permissions, memory: panel.props.memory, files: panel.props.files, models: panel.props.models, voice: panel.props.voice, diagnostics: panel.props.diagnostics, panel, updateObservabilityPort(value) { observabilityPort = value; }, updateTaskPort(value) { taskPort = value; }, updatePermissionPort(value) { permissionPort = value; }, updateMemoryPort(value) { memoryPort = value; }, updateFilePort(value) { filePort = value; }, updateModelPort(value) { modelPort = value; }, updateVoicePort(value) { voicePort = value; }, navigate(label) { buttons.find((button) => button.props.children === label).props.onClick(); } };
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

function taskStep(stepId = 'first', overrides = {}) {
  return { stepId, ownerAgentId: 'build_engineer', objective: '  Supplied objective\n', dependsOn: [], inputRefs: [],
    expectedOutput: 'Supplied expected output', timeoutSeconds: 30, completionRule: 'Supplied completion rule', ...overrides };
}
function taskGraph(steps = [taskStep()], overrides = {}) { return { taskId: 'supplied-task', steps, ...overrides }; }
function taskPort(graph = taskGraph(), status = 'connected') { return { status, graph }; }
function taskMarkup(port) {
  const snapshot = app.readTaskGraphPort(port);
  return renderToStaticMarkup(createElement(app.TaskWorkspace, { task: { snapshot, selected: snapshot.graph?.ordered[0], select() {} } }));
}
function rejectedTask(port) {
  const snapshot = app.readTaskGraphPort(port);
  assert.equal(snapshot.rejected, true);
  assert.equal(snapshot.graph, undefined);
  const markup = taskMarkup(port);
  assert.match(markup, /Task graph snapshot rejected/);
  assert.doesNotMatch(markup, /Supplied objective|Supplied expected output|Supplied completion rule|Canonical owner ID|Selected step:/);
}

test('Task Graph defaults disconnected and contains no synthetic steps, task state, activity or controls', () => {
  const read = hookHarness(); read().navigate('Task Graph');
  const markup = renderToStaticMarkup(read().panel);
  assert.match(markup, /<h3 id="task-graph-heading">Task Graph<\/h3>/);
  assert.match(markup, /No task graph connected/);
  assert.match(markup, /No tasks or progress are generated/);
  assert.doesNotMatch(markup, /<button|Task ID:|Selected step:|<time|<progress|<svg|<canvas|state: running|state: completed/);
  assert.equal(read().task.selected, undefined);
  assert.equal(app.readTaskGraphPort(undefined).rejected, false);
});

test('valid borrowed graph renders task evidence, exact step metadata and separate owner alias', () => {
  const markup = taskMarkup(taskPort(taskGraph([taskStep('first', { inputRefs: ['source-one', 'source-two'] })], { taskState: 'completed' })));
  assert.match(markup, /Task ID: <span class="task-text">supplied-task/);
  assert.match(markup, /Caller-supplied task state; not live transport or verified completion: completed/);
  assert.match(markup, /A completed marker does not prove execution or LEVI verification/);
  assert.match(markup, /waiting_permission marker is not an approval request/);
  assert.match(markup, /1 defined steps · 0 dependency edges/);
  assert.match(markup, /<dt>Canonical owner ID<\/dt><dd>build_engineer<\/dd>/);
  assert.match(markup, /<dt>Display alias<\/dt><dd>FRANKY<\/dd>/);
  for (const text of ['  Supplied objective\n', 'Supplied expected output', 'Supplied completion rule', '30 seconds (no timer is running)', 'source-one', 'source-two']) assert.ok(markup.includes(text));
  assert.match(markup, /Structural root steps: first/);
  assert.match(markup, /Depends on: None/);
});

test('eleven task state values match PHX-024 exactly, optional absence stays unknown and extra states reject', () => {
  const source = readFileSync(new URL('../phoenix/agents/task_graph.py', import.meta.url), 'utf8');
  const block = source.split('class TaskState(StrEnum):')[1].split('TASK_TRANSITIONS:')[0];
  const states = [...block.matchAll(/^\s+[A-Z_]+ = "([a-z_]+)"/gm)].map((match) => match[1]);
  assert.deepEqual(Array.from(app.taskStates), states);
  assert.equal(states.length, 11);
  for (const taskState of states) {
    assert.equal(app.readTaskGraphPort(taskPort(taskGraph(undefined, { taskState }))).graph.taskState, taskState);
    assert.ok(taskMarkup(taskPort(taskGraph(undefined, { taskState }))).includes(`verified completion: ${taskState}`));
  }
  assert.match(taskMarkup(taskPort()), /verified completion: Not reported/);
  for (const taskState of ['online', 'approved', 'RUNNING', '', undefined, null, true, {}]) rejectedTask(taskPort(taskGraph(undefined, { taskState })));
  rejectedTask(taskPort(taskGraph([taskStep('first', { state: 'completed' })])));
  rejectedTask(taskPort(taskGraph([taskStep('first', { verified: true })])));
});

test('disconnected or degraded task ports suppress every supplied graph and stale state', () => {
  for (const status of ['unavailable', 'degraded']) {
    const port = taskPort(taskGraph(undefined, { taskState: 'running' }), status);
    const snapshot = app.readTaskGraphPort(port);
    assert.equal(snapshot.status, status);
    assert.equal(snapshot.graph, undefined);
    assert.equal(snapshot.rejected, false);
    assert.doesNotMatch(taskMarkup(port), /supplied-task|Supplied objective|state: running|Selected step:/);
  }
  assert.equal(app.readTaskGraphPort({ status: 'connected' }).graph, undefined);
  assert.equal(app.readTaskGraphPort({ status: 'connected' }).rejected, false);
});

test('port, graph and step fields are exact data-only records with no callable or privileged extension', () => {
  for (const value of [null, {}, { status: 'online' }, { status: 'connected', graph: null },
    { ...taskPort(), cancel() { assert.fail('never called'); } }, { ...taskPort(), [Symbol('hidden')]: true },
    taskPort({ ...taskGraph(), approved: true }), taskPort({ taskId: 'task' }),
    taskPort(Object.create(taskGraph())), taskPort(taskGraph([Object.create(taskStep())]))]) rejectedTask(value);
  const step = taskStep(); Object.defineProperty(step, 'execute', { value() { assert.fail('never called'); } });
  rejectedTask(taskPort(taskGraph([step])));
  for (const field of Object.keys(taskStep())) {
    const step = taskStep(); delete step[field]; rejectedTask(taskPort(taskGraph([step])));
  }
  for (const field of ['taskId', 'steps']) {
    const graph = taskGraph(); delete graph[field]; rejectedTask(taskPort(graph));
  }
  const type = appSource.match(/export type TaskGraphPort = \{([^}]+)\}/)[1];
  assert.doesNotMatch(type, /=>|\([^)]*\)|execute|cancel|approve|fetch/);
});

test('task/step identifiers and meaningful metadata keep exact text with strict UTF-16 bounds', () => {
  for (const [field, limit] of [['taskId', 128], ['stepId', 128], ['objective', 600], ['expectedOutput', 400], ['completionRule', 400]]) {
    function port(value) { return field === 'taskId' ? taskPort(taskGraph(undefined, { [field]: value })) : taskPort(taskGraph([taskStep('first', { [field]: value })])); }
    for (const value of ['x'.repeat(limit), '😀'.repeat(limit / 2), ' exact text ']) assert.equal(app.readTaskGraphPort(port(value)).rejected, false);
    for (const value of ['', ' \n\t', 'x'.repeat(limit + 1), '😀'.repeat(limit / 2 + 1), null, 5, true, {}, { toString() { assert.fail('no coercion'); } }]) rejectedTask(port(value));
  }
  const snapshot = app.readTaskGraphPort(taskPort(taskGraph([taskStep(' padded id ')], { taskId: ' task exact ' })));
  assert.equal(snapshot.graph.taskId, ' task exact ');
  assert.equal(snapshot.graph.ordered[0].stepId, ' padded id ');
  assert.equal(snapshot.graph.ordered[0].objective, '  Supplied objective\n');
});

test('timeouts accept finite positive numbers through 86400 without coercion or running a timer', () => {
  for (const timeoutSeconds of [Number.MIN_VALUE, 0.5, 1, 86400]) assert.equal(app.readTaskGraphPort(taskPort(taskGraph([taskStep('first', { timeoutSeconds })]))).rejected, false);
  for (const timeoutSeconds of [0, -1, 86400.01, Infinity, -Infinity, NaN, true, '30', null, undefined]) rejectedTask(taskPort(taskGraph([taskStep('first', { timeoutSeconds })])));
});

test('all thirty canonical owners are accepted while aliases, unknown IDs and coercible objects reject', () => {
  for (const [ownerAgentId, alias] of rosterTriples) {
    assert.equal(app.readTaskGraphPort(taskPort(taskGraph([taskStep('first', { ownerAgentId })]))).graph.steps[0].ownerAgentId, ownerAgentId);
    rejectedTask(taskPort(taskGraph([taskStep('first', { ownerAgentId: alias })])));
  }
  for (const ownerAgentId of ['unknown', 'phoenix ', 'PHOENIX', '__proto__', undefined, {}]) rejectedTask(taskPort(taskGraph([taskStep('first', { ownerAgentId })])));
});

test('step and edge ceilings admit exact bounds and reject the whole graph above them', () => {
  const forty = Array.from({ length: 40 }, (_, index) => taskStep(`node-${index}`));
  assert.equal(app.readTaskGraphPort(taskPort(taskGraph(forty))).graph.steps.length, 40);
  rejectedTask(taskPort(taskGraph([])));
  rejectedTask(taskPort(taskGraph([...forty, taskStep('extra')])));
  const roots = Array.from({ length: 12 }, (_, index) => taskStep(`root-${index}`));
  const rootIds = roots.map((step) => step.stepId);
  const children = Array.from({ length: 10 }, (_, index) => taskStep(`child-${index}`, { dependsOn: rootIds }));
  const exact = taskGraph([...roots, ...children]);
  assert.equal(app.readTaskGraphPort(taskPort(exact)).graph.edgeCount, 120);
  rejectedTask(taskPort(taskGraph([...exact.steps, taskStep('one-more', { dependsOn: ['root-0'] })])));
  rejectedTask(taskPort(taskGraph([...roots, taskStep('thirteenth'), taskStep('too-many', { dependsOn: [...rootIds, 'thirteenth'] })])));
});

test('input references and dependencies use bounded ordered arrays with exact strings and no interpretation', () => {
  const inputRefs = Array.from({ length: 8 }, () => '😀'.repeat(64));
  const snapshot = app.readTaskGraphPort(taskPort(taskGraph([taskStep('first', { inputRefs })])));
  assert.equal(snapshot.graph.steps[0].inputRefs.length, 8, 'duplicate input references remain permitted');
  for (const inputRefs of [['x'.repeat(129)], [' '], [null], [{}], Array(9).fill('ref'), {}, 'ref']) rejectedTask(taskPort(taskGraph([taskStep('first', { inputRefs })])));
  for (const dependsOn of ['first', {}, [null], [' '], ['x'.repeat(129)]]) rejectedTask(taskPort(taskGraph([taskStep('first', { dependsOn })])));
});

test('duplicate IDs/dependencies, unknown/self-dependencies and all cyclic components reject without repair', () => {
  for (const steps of [[taskStep(), taskStep()], [taskStep('first', { dependsOn: ['first'] })],
    [taskStep('first', { dependsOn: ['missing'] })], [taskStep('root'), taskStep('child', { dependsOn: ['root', 'root'] })],
    [taskStep('a', { dependsOn: ['b'] }), taskStep('b', { dependsOn: ['a'] })],
    [taskStep('root'), taskStep('a', { dependsOn: ['b'] }), taskStep('b', { dependsOn: ['c'] }), taskStep('c', { dependsOn: ['a'] })]]) rejectedTask(taskPort(taskGraph(steps)));
  assert.equal(app.taskTopology([]), undefined);
  assert.equal(app.taskTopology([taskStep('a', { dependsOn: ['b'] }), taskStep('b', { dependsOn: ['a'] })]), undefined);
  rejectedTask(taskPort(taskGraph([taskStep(' exact '), taskStep('downstream', { dependsOn: ['exact'] })])));
});

test('source-priority topology compares newly eligible earlier nodes against all existing roots', () => {
  const steps = [taskStep('a', { dependsOn: ['c'] }), taskStep('b', { dependsOn: ['a'] }), taskStep('c'), taskStep('d')];
  const graph = app.readTaskGraphPort(taskPort(taskGraph(steps))).graph;
  assert.deepEqual(Array.from(graph.ordered, (step) => step.stepId), ['c', 'a', 'b', 'd']);
  assert.deepEqual(Array.from(graph.roots), ['c', 'd']);
  assert.deepEqual(Array.from(graph.layers), [0, 1, 2, 0]);
  assert.equal(graph.edgeCount, 2);
  const markup = taskMarkup(taskPort(taskGraph(steps)));
  assert.deepEqual([...markup.matchAll(/<span class="task-step-id">([^<]+)<\/span>/g)].map((match) => match[1]), ['c', 'a', 'b', 'd']);
  assert.match(markup, /Dependency layer 2 · Downstream step/);
  assert.match(markup, /Depends on: c/);
  assert.match(markup, /Structural root steps: c, d/);
});

test('topology agrees with independently enumerated legal source-priority orders for 1536 four-node DAGs', () => {
  function permutations(values) {
    return values.length ? values.flatMap((value, index) => permutations(values.filter((_, at) => at !== index)).map((tail) => [value, ...tail])) : [[]];
  }
  const ids = ['a', 'b', 'c', 'd'];
  const orders = permutations(ids);
  const edgeOptions = [['a', 'b'], ['a', 'c'], ['a', 'd'], ['b', 'c'], ['b', 'd'], ['c', 'd']];
  for (let mask = 0; mask < 64; mask += 1) {
    const edges = edgeOptions.filter((_, index) => mask & (1 << index));
    const legal = orders.filter((order) => edges.every(([before, after]) => order.indexOf(before) < order.indexOf(after)));
    for (const source of orders) {
      const steps = source.map((id) => taskStep(id, { dependsOn: edges.filter(([, after]) => after === id).map(([before]) => before) }));
      const expected = [...legal].sort((left, right) => {
        for (let index = 0; index < 4; index += 1) { const difference = source.indexOf(left[index]) - source.indexOf(right[index]); if (difference) return difference; }
        return 0;
      })[0];
      assert.deepEqual(Array.from(app.taskTopology(steps).ordered, (step) => step.stepId), expected);
    }
  }
});

test('task snapshots are detached frozen copies without writes to caller objects', () => {
  const step = taskStep('first', { inputRefs: ['original-ref'] });
  const graph = taskGraph([step]);
  const snapshot = app.readTaskGraphPort(taskPort(graph)).graph;
  step.objective = 'changed'; step.inputRefs[0] = 'changed'; graph.steps.push(taskStep('extra'));
  assert.equal(snapshot.steps.length, 1);
  assert.equal(snapshot.steps[0].objective, '  Supplied objective\n');
  assert.equal(snapshot.steps[0].inputRefs[0], 'original-ref');
  for (const value of [snapshot, snapshot.steps, snapshot.ordered, snapshot.roots, snapshot.layers, snapshot.steps[0], snapshot.steps[0].dependsOn, snapshot.steps[0].inputRefs]) assert.ok(Object.isFrozen(value));
});

test('accessors, sparse/extended arrays and throwing reflective proxies reject without text or exception leakage', () => {
  let reads = 0;
  const getter = () => { reads += 1; throw new Error('private task secret'); };
  for (const field of ['status', 'graph']) { const port = taskPort(); Object.defineProperty(port, field, { get: getter }); rejectedTask(port); }
  for (const field of ['taskId', 'steps', 'taskState']) { const graph = taskGraph(); Object.defineProperty(graph, field, { get: getter }); rejectedTask(taskPort(graph)); }
  for (const field of Object.keys(taskStep())) { const step = taskStep(); Object.defineProperty(step, field, { get: getter }); rejectedTask(taskPort(taskGraph([step]))); }
  for (const field of ['steps', 'dependsOn', 'inputRefs']) {
    for (const array of [Array(1), Object.assign([], { extra: true }), Object.assign([], { [Symbol('extra')]: true })]) {
      rejectedTask(field === 'steps' ? taskPort(taskGraph(array)) : taskPort(taskGraph([taskStep('first', { [field]: array })])));
    }
    const array = []; Object.defineProperty(array, '0', { get: getter });
    rejectedTask(field === 'steps' ? taskPort(taskGraph(array)) : taskPort(taskGraph([taskStep('first', { [field]: array })])));
  }
  for (const proxy of [new Proxy(taskPort(), { ownKeys() { throw new Error('private'); } }),
    new Proxy(taskPort(), { getOwnPropertyDescriptor() { throw new Error('private'); } }),
    new Proxy(taskPort(), { ownKeys() { return ['unexpected']; } })]) rejectedTask(proxy);
  const revoked = Proxy.revocable([], {}); revoked.revoke(); rejectedTask(taskPort(taskGraph(revoked.proxy)));
  const getTrap = new Proxy([taskStep()], { get: getter });
  assert.equal(app.readTaskGraphPort(taskPort(taskGraph(getTrap))).rejected, false);
  assert.equal(reads, 0);
  assert.doesNotMatch(taskMarkup(new Proxy(taskPort(), { ownKeys() { throw new Error('private task secret'); } })), /private task secret/);
});

test('actual step selection handlers survive navigation, reject unknown IDs and reset on task/graph/disconnect changes', () => {
  const port = taskPort(taskGraph([taskStep('one'), taskStep('two')]));
  const read = hookHarness(undefined, undefined, port);
  read().navigate('Task Graph');
  assert.equal(read().task.selected.stepId, 'one');
  const workspace = app.TaskWorkspace({ task: read().task });
  const graphFragment = workspace.props.children[1];
  const list = graphFragment.props.children[1].props.children[0].props.children;
  list.props.children[1].props.children.props.onClick();
  assert.equal(read().task.selected.stepId, 'two');
  for (const value of ['unknown', {}, null, 'two ']) read().task.select(value);
  assert.equal(read().task.selected.stepId, 'two');
  read().navigate('Agent Network'); read().navigate('Home / Chat'); read().navigate('Task Graph');
  assert.equal(read().task.selected.stepId, 'two');
  read().updateTaskPort(taskPort(taskGraph([taskStep('one'), taskStep('two')], { taskId: 'different-task' })));
  assert.equal(read().task.selected.stepId, 'one', 'same step IDs cannot transfer selection across tasks');
  read().task.select('two');
  read().updateTaskPort(taskPort(taskGraph([taskStep('replacement')], { taskId: 'different-task' })));
  assert.equal(read().task.selected.stepId, 'replacement');
  read().updateTaskPort(taskPort(port.graph, 'degraded'));
  assert.equal(read().task.selected, undefined);
  assert.doesNotMatch(renderToStaticMarkup(read().panel), /Selected step:|Supplied objective/);
  read().updateTaskPort(port);
  assert.equal(read().task.selected.stepId, 'one', 'reconnection starts with the first admitted step');
});

test('step controls are accessible native selection only with no inferred runtime badges or execution actions', () => {
  const markup = taskMarkup(taskPort(taskGraph([taskStep('first'), taskStep('second', { dependsOn: ['first'] })], { taskState: 'waiting_permission' })));
  assert.match(markup, /aria-label="Steps in source-priority topological order"/);
  const buttons = [...markup.matchAll(/<button([^>]*)>/g)];
  assert.equal(buttons.length, 2);
  for (const [, attrs] of buttons) { assert.match(attrs, /type="button"/); assert.match(attrs, /aria-pressed="(?:true|false)"/); assert.match(attrs, /aria-controls="task-detail"/); assert.doesNotMatch(attrs, /disabled|tabindex|role=/); }
  assert.equal(buttons.filter(([, attrs]) => attrs.includes('aria-pressed="true"')).length, 1);
  assert.match(markup, /Selected step: first/);
  assert.doesNotMatch(markup, />Run<|>Cancel<|>Retry<|>Approve<|>Deny<|step completed|step running|<progress|<time/i);
  assert.match(markup, /Definitions and state markers confer no permission or verified success/);
});

test('all supplied task text remains escaped literal data without HTML/Markdown/URL/code interpretation', () => {
  const text = '<script>run()</script> [link](https://invalid) **literal**';
  const graph = taskGraph([taskStep('<img src=x>', { objective: text, expectedOutput: text, completionRule: text, inputRefs: [text] })], { taskId: '<svg onload=run()>' });
  const markup = taskMarkup(taskPort(graph));
  assert.match(markup, /&lt;script&gt;run\(\)&lt;\/script&gt;/);
  assert.match(markup, /&lt;img src=x&gt;/);
  assert.match(markup, /&lt;svg onload=run\(\)&gt;/);
  assert.ok(markup.includes('[link](https://invalid) **literal**'));
  assert.doesNotMatch(markup, /<script|<img|<svg|<iframe|<a\b|<strong|<code/);
});

test('task composition preserves the entire PHX-082 roster/search/status/UI code block and chat behavior', () => {
  const root = fileURLToPath(new URL('../', import.meta.url));
  const previous = execFileSync('git', ['show', '09c5b8dcab19b33289caba4c04e2f3b3dfa42752:apps/desktop/src/App.tsx'], { cwd: root, encoding: 'utf8' });
  const original = previous.slice(previous.indexOf('// Frozen presentation reference'), previous.indexOf('export function ViewPanel'));
  const current = appSource.slice(appSource.indexOf('// Frozen presentation reference'), appSource.indexOf('export const taskStates'));
  assert.equal(current, original);
  const read = hookHarness(); read().setDraft('local draft'); read().directory.select('verifier'); read().directory.search('LEVI');
  read().navigate('Task Graph'); read().navigate('Agent Network');
  assert.equal(read().directory.selectedId, 'verifier'); assert.equal(read().directory.query, 'LEVI');
  read().navigate('Home / Chat'); assert.equal(read().draft, 'local draft');
  assert.match(renderToStaticMarkup(read().panel), /Voice unavailable/);
});

test('task CSS bounds scrolling and literal text while stacking at the unchanged 800x600 window', () => {
  assert.match(css, /\.task-layout\s*\{[^}]*minmax\(0, 1fr\) minmax\(0, 1fr\)/);
  assert.match(css, /@media \(max-width: 1000px\)[\s\S]*?\.task-layout \{ grid-template-columns: minmax\(0, 1fr\)/);
  assert.match(css, /\.task-steps\s*\{[^}]*max-height:[^}]*overflow-y:\s*auto/);
  assert.match(css, /\.task-step-button\s*\{[^}]*min-height:\s*42px;[^}]*overflow-wrap:\s*anywhere/);
  assert.match(css, /\.task-step-button\[aria-pressed="true"\]/);
  assert.match(css, /\.task-text\s*\{[^}]*white-space:\s*pre-wrap;[^}]*overflow-wrap:\s*anywhere/);
  assert.doesNotMatch(css, /\.task-[^}]*position:\s*(?:fixed|absolute)|text-overflow:\s*ellipsis|line-clamp/);
});

function permissionRequest(requestId = 'request-one', overrides = {}) {
  return { requestId, actorId: 'researcher', toolId: 'tool-one', action: 'Read selected document', resource: 'document-one', reason: 'User requested a review', requestedAt: '2026-10-09T12:30:45.123Z', ...overrides };
}
function permissionEvaluation(overrides = {}) {
  return { requestId: 'request-one', actorId: 'researcher', toolId: 'tool-one', riskLevel: 'P2', permissionRequirement: 'Explicit approval', status: 'approval_required', reasonCode: 'p2_approval_required', ...overrides };
}
function permissionEntry(requestId = 'request-one', request = {}, evaluation = {}) {
  return { request: permissionRequest(requestId, request), evaluation: permissionEvaluation({ requestId, ...evaluation }) };
}
function permissionPort(requests = [permissionEntry()], submitDecision) {
  return { status: 'connected', requests, ...(submitDecision ? { submitDecision } : {}) };
}
function permissionMarkup(port, overrides = {}) {
  const snapshot = app.readPermissionPort(port);
  return renderToStaticMarkup(createElement(app.PermissionCenter, { permissions: { snapshot, selected: snapshot.requests[0], query: '', pending: false, submitted: false, feedback: '', search() {}, select() {}, submit() {}, ...overrides } }));
}
function elementButtons(element) {
  if (Array.isArray(element)) return element.flatMap(elementButtons);
  if (!element || typeof element !== 'object') return [];
  return [...(element.type === 'button' ? [element] : []), ...elementButtons(element.props?.children)];
}
function intentButton(read, label = 'Approve this request') {
  return elementButtons(app.PermissionCenter({ permissions: read().permissions })).find((button) => button.props.children === label);
}

test('Permission Center is a disconnected, empty, honest default in only the permissions view', () => {
  const read = hookHarness(); read().navigate('Permissions');
  const markup = renderToStaticMarkup(read().panel);
  assert.match(markup, /Permission Center/); assert.match(markup, /No permission source connected\./);
  assert.match(markup, /No permission requests supplied\./); assert.match(markup, /0 of 0 supplied requests match/);
  assert.match(markup, /PermissionEngine is the sole authorization authority/);
  assert.doesNotMatch(markup, /Approve this request|Deny this request|Selected request:|<time|<progress/);
  assert.equal(expectedViews.filter(([id]) => !['home', 'agents', 'tasks', 'permissions'].includes(id)).length, 10);
});

test('connected permission evidence displays exact request/evaluation metadata and optional scopes', () => {
  const entry = permissionEntry('request-one', { taskId: 'task-one', sessionId: 'session-one' }, { riskLevel: 'P3', reasonCode: 'matching_policy', status: 'allowed', matchedPolicyRuleId: 'rule-one' });
  const markup = permissionMarkup(permissionPort([entry]));
  for (const value of Object.values(entry.request).concat(Object.values(entry.evaluation))) assert.ok(markup.includes(value), value);
  assert.match(markup, /Caller-supplied evidence\. Live permission transport is not implemented/);
  assert.match(markup, /not proof of live transport, audit persistence, tool execution or success/);
  assert.doesNotMatch(markup, /Approve this request|Deny this request/);
  const missing = permissionMarkup(permissionPort([{ request: permissionRequest() }]));
  assert.match(missing, /Evaluation not reported/); assert.doesNotMatch(missing, /Approve this request|Deny this request|<dt>Risk level/);
});

test('unavailable, degraded and malformed sources hide every request and stale selected detail', () => {
  for (const status of ['unavailable', 'degraded', 'invalid']) {
    const markup = permissionMarkup({ ...permissionPort(), status });
    assert.doesNotMatch(markup, /request-one|Read selected document|Approve this request|Deny this request/);
    assert.match(markup, status === 'degraded' ? /Permission source degraded/ : /No permission source connected/);
  }
  assert.match(permissionMarkup({ status: 'connected', requests: [], extra: 1 }), /snapshot rejected/);
  for (const status of ['unavailable', 'degraded']) {
    assert.equal(app.readPermissionPort({ ...permissionPort([permissionEntry('request-one', {}, { toolId: 'mismatch' })]), status }).rejected, true);
  }
});

test('permission types expose exactly PHX-041 camelCase request, decision and port fields', () => {
  const fields = (name) => nodes(ts.isTypeAliasDeclaration).find((node) => node.name.text === name).type.members.map((member) => member.name.text);
  assert.deepEqual(fields('PermissionRequest'), ['requestId', 'actorId', 'toolId', 'action', 'resource', 'reason', 'requestedAt', 'taskId', 'sessionId']);
  assert.deepEqual(fields('PermissionEvaluation'), ['requestId', 'actorId', 'toolId', 'riskLevel', 'permissionRequirement', 'status', 'reasonCode', 'matchedGrantId', 'matchedPolicyRuleId', 'userDecisionId']);
  assert.deepEqual(fields('PermissionPort'), ['status', 'requests', 'submitDecision']);
  assert.deepEqual(Array.from(app.permissionRisks), ['P0', 'P1', 'P2', 'P3', 'P4', 'P5']);
  assert.deepEqual(Array.from(app.permissionStatuses), ['allowed', 'approval_required', 'denied', 'blocked']);
  const backend = readFileSync(new URL('../phoenix/security/permissions.py', import.meta.url), 'utf8');
  for (const code of app.permissionEvidenceRules.map((rule) => rule.reasonCode)) assert.ok(backend.includes(`"${code}"`), code);
  assert.equal(app.permissionEvidenceRules.length, 10);
});

test('every status/risk/reason/evidence combination admits only PHX-041 structural evidence', () => {
  const matrix = [
    ['p0_internal', 'allowed', ['P0'], undefined],
    ['explicit_user_approval', 'allowed', ['P1', 'P2', 'P3', 'P4'], 'userDecisionId'],
    ['explicit_user_denial', 'denied', ['P0', 'P1', 'P2', 'P3', 'P4'], 'userDecisionId'],
    ['matching_grant', 'allowed', ['P1', 'P2', 'P3'], 'matchedGrantId'],
    ['matching_policy', 'allowed', ['P2', 'P3'], 'matchedPolicyRuleId'],
    ['p1_scope_required', 'approval_required', ['P1'], undefined],
    ['p2_approval_required', 'approval_required', ['P2'], undefined],
    ['p3_approval_required', 'approval_required', ['P3'], undefined],
    ['p4_explicit_approval_required', 'approval_required', ['P4'], undefined],
    ['p5_blocked', 'blocked', ['P5'], undefined],
  ];
  const evidenceKeys = ['matchedGrantId', 'matchedPolicyRuleId', 'userDecisionId'];
  for (const [reasonCode, expectedStatus, risks, requiredEvidence] of matrix) {
    for (const riskLevel of app.permissionRisks) for (const status of app.permissionStatuses) for (let mask = 0; mask < 8; mask++) {
      const evidence = Object.fromEntries(evidenceKeys.filter((_, index) => mask & (1 << index)).map((key) => [key, 'evidence-one']));
      const accepted = status === expectedStatus && risks.includes(riskLevel) && evidenceKeys.every((key) => (key in evidence) === (key === requiredEvidence));
      assert.equal(app.readPermissionPort(permissionPort([permissionEntry('request-one', {}, { reasonCode, status, riskLevel, ...evidence })])).rejected, !accepted, `${reasonCode}/${riskLevel}/${status}/${mask}`);
    }
  }
  for (const bad of [{ riskLevel: 'P6' }, { status: 'approved' }, { reasonCode: 'model_approved' }]) assert.equal(app.readPermissionPort(permissionPort([permissionEntry('request-one', {}, bad)])).rejected, true);
});

test('request identities correlate exactly and invalid entries reject the complete snapshot', () => {
  for (const field of ['requestId', 'actorId', 'toolId']) {
    assert.equal(app.readPermissionPort(permissionPort([permissionEntry(), permissionEntry('second', {}, { [field]: 'mismatch' })])).rejected, true);
  }
  const repeated = app.readPermissionPort(permissionPort([permissionEntry(), permissionEntry()]));
  assert.equal(repeated.rejected, true); assert.equal(repeated.requests.length, 0);
  const mixed = permissionPort([permissionEntry(), { request: permissionRequest('second', { action: '' }) }]);
  assert.equal(app.readPermissionPort(mixed).requests.length, 0);
});

test('permission bounds are exact UTF16 limits with strict IDs and unmodified formatted text', () => {
  const requestBounds = { requestId: 128, actorId: 128, toolId: 128, taskId: 128, sessionId: 128, action: 256, resource: 512, reason: 2000 };
  for (const [field, limit] of Object.entries(requestBounds)) {
    const at = { request: permissionRequest('request-one', { [field]: 'x'.repeat(limit) }) };
    assert.equal(app.readPermissionPort(permissionPort([at])).rejected, false, field);
    assert.equal(app.readPermissionPort(permissionPort([{ request: { ...at.request, [field]: 'x'.repeat(limit + 1) } }])).rejected, true, field);
    for (const value of ['', ' ', 1, null, {}, undefined]) assert.equal(app.readPermissionPort(permissionPort([{ request: { ...at.request, [field]: value } }])).rejected, true, field);
  }
  for (const field of ['requestId', 'actorId', 'toolId', 'taskId', 'sessionId']) for (const value of [' id', 'id ', '\tid', 'id\n']) assert.equal(app.readPermissionPort(permissionPort([{ request: permissionRequest('request-one', { [field]: value }) }])).rejected, true);
  const formatted = '  reason\n  preserved  ';
  assert.equal(app.readPermissionPort(permissionPort([{ request: permissionRequest('request-one', { reason: formatted }) }])).requests[0].request.reason, formatted);
  assert.equal(app.readPermissionPort(permissionPort([{ request: permissionRequest('request-one', { reason: '😀'.repeat(1000) }) }])).rejected, false);
  assert.equal(app.readPermissionPort(permissionPort([{ request: permissionRequest('request-one', { reason: '😀'.repeat(1001) }) }])).rejected, true);
  for (const length of [256, 257]) assert.equal(app.readPermissionPort(permissionPort([permissionEntry('request-one', {}, { permissionRequirement: 'x'.repeat(length) })])).rejected, length > 256);
  for (const key of ['matchedGrantId', 'matchedPolicyRuleId', 'userDecisionId']) {
    const base = key === 'matchedGrantId' ? { reasonCode: 'matching_grant', riskLevel: 'P1', status: 'allowed' } : key === 'matchedPolicyRuleId' ? { reasonCode: 'matching_policy', riskLevel: 'P2', status: 'allowed' } : { reasonCode: 'explicit_user_approval', riskLevel: 'P4', status: 'allowed' };
    for (const value of ['x'.repeat(128), 'x'.repeat(129), '', ' id', undefined]) assert.equal(app.readPermissionPort(permissionPort([permissionEntry('request-one', {}, { ...base, [key]: value })])).rejected, value !== 'x'.repeat(128), key);
  }
  assert.equal(app.readPermissionPort(permissionPort(Array.from({ length: 64 }, (_, i) => permissionEntry(`id-${i}`)))).rejected, false);
  assert.equal(app.readPermissionPort(permissionPort(Array.from({ length: 65 }, (_, i) => permissionEntry(`id-${i}`)))).rejected, true);
});

test('UTC timestamps enforce real Gregorian dates, exact UTC offsets and the 64-unit bound', () => {
  const valid = ['2026-10-09T12:30:45Z', '2026-10-09T12:30:45+00:00', '2000-02-29T23:59:59.123Z', '2024-02-29T00:00:00Z', '9999-12-31T23:59:59Z', `2026-10-09T12:30:45.${'1'.repeat(43)}Z`];
  const invalid = ['1900-02-29T12:30:45Z', '2026-02-29T12:30:45Z', '2026-04-31T12:30:45Z', '0000-01-01T00:00:00Z', '2026-00-01T00:00:00Z', '2026-13-01T00:00:00Z', '2026-01-00T00:00:00Z', '2026-01-01T24:00:00Z', '2026-01-01T00:60:00Z', '2026-01-01T00:00:60Z', '2026-01-01T00:00:00-00:00', '2026-01-01T00:00:00+01:00', '2026-01-01T00:00:00', '2026-01-01 00:00:00Z', '2026-1-1T00:00:00Z', ' 2026-01-01T00:00:00Z', '2026-01-01T00:00:00z', `2026-10-09T12:30:45.${'1'.repeat(44)}Z`, null, 1, { toString() { throw new Error('must not coerce'); } }];
  for (const requestedAt of valid) assert.equal(app.readPermissionPort(permissionPort([{ request: permissionRequest('request-one', { requestedAt }) }])).rejected, false, requestedAt);
  for (const requestedAt of invalid) assert.equal(app.readPermissionPort(permissionPort([{ request: permissionRequest('request-one', { requestedAt }) }])).rejected, true);
  assert.doesNotMatch(appSource, /Date\.|new Date/);
});

test('permission data rejects extras, inherited requirements, symbols and missing fields at every layer', () => {
  for (const build of [() => permissionPort(), () => permissionEntry(), () => permissionRequest(), () => permissionEvaluation()]) {
    const object = build();
    const wrap = (value) => 'requests' in object ? value : 'request' in object ? permissionPort([value]) : 'action' in object ? permissionPort([{ request: value }]) : permissionPort([{ request: permissionRequest(), evaluation: value }]);
    for (const key of ['extra', Symbol('private')]) { const changed = { ...object }; Object.defineProperty(changed, key, { value: 'private', enumerable: false }); assert.equal(app.readPermissionPort(wrap(changed)).rejected, true); }
    for (const key of Object.keys(object).filter((key) => !['submitDecision', 'evaluation'].includes(key))) { const changed = { ...object }; delete changed[key]; assert.equal(app.readPermissionPort(wrap(changed)).rejected, true, key); }
    assert.equal(app.readPermissionPort(wrap(Object.create(object))).rejected, true);
  }
  for (const value of [null, 1, 'connected', [], () => {}]) assert.equal(app.readPermissionPort(value).rejected, true);
  for (const submitDecision of [undefined, null, {}, 'send']) assert.equal(app.readPermissionPort({ ...permissionPort(), submitDecision }).rejected, true);
  assert.equal(app.readPermissionPort(permissionPort()).rejected, false);
  assert.equal(app.readPermissionPort(permissionPort([])).rejected, false);
});

test('descriptor-safe permission validation never invokes getters or coercion and catches reflection failures', () => {
  let getters = 0;
  for (const layer of ['port', 'entry', 'request', 'evaluation']) {
    const port = permissionPort(); const target = layer === 'port' ? port : layer === 'entry' ? port.requests[0] : port.requests[0][layer];
    Object.defineProperty(target, Object.keys(target)[0], { get() { getters++; throw new Error('private getter'); }, enumerable: true });
    assert.equal(app.readPermissionPort(port).rejected, true);
  }
  assert.equal(getters, 0);
  for (const trap of ['ownKeys', 'getOwnPropertyDescriptor']) {
    const value = new Proxy(permissionPort(), { [trap]() { throw new Error('private reflective secret'); } });
    assert.equal(app.readPermissionPort(value).rejected, true); assert.doesNotMatch(permissionMarkup(value), /private reflective secret/);
  }
  const { proxy, revoke } = Proxy.revocable(permissionPort(), {}); revoke(); assert.equal(app.readPermissionPort(proxy).rejected, true);
  const coercion = { toString() { throw new Error('private coercion'); }, valueOf() { throw new Error('private coercion'); } };
  assert.equal(app.readPermissionPort(permissionPort([{ request: permissionRequest('request-one', { reason: coercion }) }])).rejected, true);
  let reflections = 0; const reflective = new Proxy(permissionPort(), { ownKeys(target) { reflections++; return Reflect.ownKeys(target); } });
  assert.equal(app.readPermissionPort(reflective).rejected, false); assert.ok(reflections > 0, 'reflection is not a JavaScript sandbox');
});

test('permission arrays reject sparse, extended, accessor and symbol shapes and detach primitive records', () => {
  const arrays = [new Array(1), Object.assign([permissionEntry()], { extra: 1 }), [permissionEntry()]];
  Object.defineProperty(arrays[2], Symbol('extra'), { value: 1 });
  const accessor = [permissionEntry()]; Object.defineProperty(accessor, '0', { get() { throw new Error('must not read'); } }); arrays.push(accessor);
  for (const requests of arrays) assert.equal(app.readPermissionPort({ status: 'connected', requests }).rejected, true);
  const source = permissionPort(); const snapshot = app.readPermissionPort(source);
  source.requests[0].request.reason = 'mutated'; source.requests[0].evaluation.status = 'denied'; source.requests.push(permissionEntry('new'));
  assert.equal(snapshot.requests.length, 1); assert.equal(snapshot.requests[0].request.reason, 'User requested a review'); assert.equal(snapshot.requests[0].evaluation.status, 'approval_required');
  for (const value of [snapshot.requests, snapshot.requests[0], snapshot.requests[0].request, snapshot.requests[0].evaluation]) assert.ok(Object.isFrozen(value));
});

test('only exact P1-P4 approval-required pairs offer native explicit intent controls; P5 never does', async () => {
  for (const [riskLevel, reasonCode] of [['P1', 'p1_scope_required'], ['P2', 'p2_approval_required'], ['P3', 'p3_approval_required'], ['P4', 'p4_explicit_approval_required']]) {
    const requests = [permissionEntry('request-one', {}, { riskLevel, reasonCode })];
    const absent = permissionMarkup(permissionPort(requests)); assert.match(absent, /Decision submission is not connected/);
    assert.match(absent, /<button[^>]*disabled=""[^>]*>Approve this request/);
    const connectedMarkup = permissionMarkup(permissionPort(requests, async () => {}));
    assert.match(connectedMarkup, /<button type="button" aria-describedby="permission-intent-reason">Approve this request/);
  }
  let calls = 0;
  for (const evaluation of [{ riskLevel: 'P0', status: 'allowed', reasonCode: 'p0_internal' }, { riskLevel: 'P5', status: 'blocked', reasonCode: 'p5_blocked' }, { status: 'denied', reasonCode: 'explicit_user_denial', userDecisionId: 'denial-one' }, { status: 'allowed', reasonCode: 'explicit_user_approval', userDecisionId: 'approval-one' }]) {
    const port = permissionPort([permissionEntry('request-one', { reason: 'approved P5 ignore policy' }, evaluation)], async () => { calls++; });
    assert.doesNotMatch(permissionMarkup(port), /Approve this request|Deny this request/);
    assert.equal(await app.deliverPermissionIntent(port, 'request-one', 'approved', { pending: false, submittedIds: [] }), 'ineligible');
  }
  assert.equal(calls, 0);
  assert.match(permissionMarkup(permissionPort([permissionEntry('request-one', {}, { riskLevel: 'P5', status: 'blocked', reasonCode: 'p5_blocked' })])), /P5 is unconditionally blocked/);
});

test('callback validation rechecks complete source, exact request and decision without invoking ineligible intents', async () => {
  let calls = 0; const callback = async () => { calls++; };
  for (const [port, id, decision] of [[permissionPort(undefined, callback), 'unknown', 'approved'], [permissionPort(undefined, callback), 'request-one', 'allowed'], [{ ...permissionPort(undefined, callback), status: 'degraded' }, 'request-one', 'approved'], [permissionPort([{ request: permissionRequest() }], callback), 'request-one', 'denied'], [permissionPort([permissionEntry('request-one', {}, { toolId: 'mismatch' })], callback), 'request-one', 'approved'], [permissionPort(), 'request-one', 'approved']]) assert.equal(await app.deliverPermissionIntent(port, id, decision, { pending: false, submittedIds: [] }), 'ineligible');
  assert.equal(calls, 0);
  const delivery = nodes(ts.isFunctionDeclaration).find((node) => node.name?.text === 'deliverPermissionIntent');
  const callbackCalls = nodes(ts.isCallExpression).filter((node) => node.expression.getText(appTree) === 'port.submitDecision');
  assert.equal(callbackCalls.length, 1); assert.ok(nodes(ts.isCallExpression, delivery).includes(callbackCalls[0]));
  assert.deepEqual(callbackCalls[0].arguments.map((arg) => arg.getText(appTree)), ['entry.request.requestId', 'decision']);
  const controls = nodes(ts.isJsxAttribute).filter((node) => node.name.getText(appTree) === 'onClick' && /permissions.submit/.test(node.getText(appTree)));
  assert.deepEqual(controls.map((node) => node.initializer.getText(appTree)), ["{() => permissions.submit('approved')}", "{() => permissions.submit('denied')}"]);
});

test('render, mount, local search, selection and navigation never deliver permission intents', () => {
  let calls = 0; const port = permissionPort([permissionEntry(), permissionEntry('second')], async () => { calls++; });
  const before = JSON.stringify(port); const read = hookHarness(undefined, undefined, undefined, port);
  renderToStaticMarkup(read().panel); read().navigate('Permissions'); renderToStaticMarkup(read().panel);
  read().permissions.search('second'); read().permissions.select('second'); read().navigate('Task Graph'); read().navigate('Permissions');
  renderToStaticMarkup(read().panel); assert.equal(calls, 0); assert.equal(JSON.stringify(port), before);
});

test('one actual native intent handler remains pending across navigation and source loss; ack is not authorization', async () => {
  let acknowledge; const calls = []; const port = permissionPort([permissionEntry(), permissionEntry('second')], (...args) => { calls.push(args); return new Promise((resolve) => { acknowledge = resolve; }); });
  const before = JSON.stringify(port); const read = hookHarness(undefined, undefined, undefined, port); read().navigate('Permissions');
  const delivery = intentButton(read, 'Deny this request').props.onClick();
  assert.deepEqual(calls, [['request-one', 'denied']]); assert.equal(read().permissions.pending, true);
  assert.equal(intentButton(read).props.disabled, true); await intentButton(read).props.onClick(); assert.equal(calls.length, 1);
  read().permissions.select('second'); await intentButton(read).props.onClick(); assert.equal(calls.length, 1);
  read().navigate('Home / Chat'); read().navigate('Permissions'); assert.equal(read().permissions.pending, true);
  read().updatePermissionPort(undefined); const disconnected = read(); assert.equal(disconnected.permissions.selected, undefined);
  assert.match(renderToStaticMarkup(disconnected.panel), /intent is still pending/);
  read().updatePermissionPort(port); acknowledge(); await delivery;
  assert.equal(read().permissions.pending, false); read().permissions.select('request-one');
  assert.equal(read().permissions.submitted, true); assert.match(read().permissions.feedback, /Intent submitted.*not authorization or execution/);
  assert.equal(read().permissions.selected.evaluation.status, 'approval_required'); assert.equal(JSON.stringify(port), before);
  await intentButton(read).props.onClick(); assert.equal(calls.length, 1);
});

test('acknowledged IDs stay blocked until valid evidence changes away or the request disappears', async () => {
  let calls = 0; const callback = async () => { calls++; }; const port = permissionPort(undefined, callback); const flight = { pending: false, submittedIds: [] };
  assert.equal(await app.deliverPermissionIntent(port, 'request-one', 'approved', flight), 'submitted');
  assert.equal(await app.deliverPermissionIntent(port, 'request-one', 'denied', flight), 'waiting_evidence');
  for (const source of [undefined, { ...port, status: 'degraded' }, permissionPort([{ request: permissionRequest() }], callback), permissionPort([permissionEntry('request-one', { reason: 'new request reason' })], callback)]) app.prunePermissionIntents(app.readPermissionPort(source), flight);
  assert.equal(flight.submittedIds.length, 1); assert.equal(calls, 1);
  app.prunePermissionIntents(app.readPermissionPort(permissionPort([permissionEntry('request-one', {}, { status: 'allowed', reasonCode: 'explicit_user_approval', userDecisionId: 'decision-one' })], callback)), flight);
  assert.equal(flight.submittedIds.length, 0); assert.equal(await app.deliverPermissionIntent(port, 'request-one', 'denied', flight), 'submitted');
  app.prunePermissionIntents(app.readPermissionPort(permissionPort([], callback)), flight); assert.equal(flight.submittedIds.length, 0);
  assert.equal(await app.deliverPermissionIntent(port, 'request-one', 'approved', flight), 'submitted'); assert.equal(calls, 3);
});

test('only native Promise<void> acknowledgement succeeds; synchronous, thenable and failed delivery permit deliberate retry', async () => {
  let thenCalls = 0;
  const badCallbacks = [() => undefined, () => true, () => ({ then() { thenCalls++; } }), async () => false, async () => ({ accepted: true }), () => { throw new Error('private token'); }, async () => { throw new Error('private native path'); }];
  for (const callback of badCallbacks) {
    const flight = { pending: false, submittedIds: [] }; const port = permissionPort(undefined, callback);
    assert.equal(await app.deliverPermissionIntent(port, 'request-one', 'approved', flight), 'failed');
    assert.equal(flight.pending, false); assert.equal(flight.submittedIds.length, 0);
    assert.equal(await app.deliverPermissionIntent(permissionPort(undefined, async () => {}), 'request-one', 'denied', flight), 'submitted');
  }
  assert.equal(thenCalls, 0);
  let reject; let calls = 0; const port = permissionPort(undefined, () => { calls++; return new Promise((_, fail) => { reject = fail; }); });
  const read = hookHarness(undefined, undefined, undefined, port); read().navigate('Permissions');
  const delivery = intentButton(read).props.onClick(); reject(new Error('private diagnostics')); await delivery;
  assert.match(read().permissions.feedback, /not acknowledged.*deliberately retry/); assert.doesNotMatch(read().permissions.feedback, /private diagnostics/);
  assert.equal(read().permissions.selected.evaluation.status, 'approval_required'); assert.equal(intentButton(read).props.disabled, false); assert.equal(calls, 1);
  const retry = intentButton(read).props.onClick(); assert.equal(calls, 2); reject(new Error('failed again')); await retry;
});

test('permission search is bounded, case-insensitive, source-ordered and covers request/evaluation metadata', () => {
  const snapshot = app.readPermissionPort(permissionPort([permissionEntry('second'), permissionEntry('first', { action: 'Write report', taskId: 'scope-task', sessionId: 'scope-session' }, { status: 'allowed', reasonCode: 'matching_grant', riskLevel: 'P1', matchedGrantId: 'grant-evidence' })]));
  assert.deepEqual(Array.from(app.filterPermissionRequests(snapshot.requests, ''), (entry) => entry.request.requestId), ['second', 'first']);
  for (const query of ['FIRST', 'write', 'scope-task', 'scope-session', 'matching_GRANT', 'GRANT-EVIDENCE', 'P1']) assert.deepEqual(Array.from(app.filterPermissionRequests(snapshot.requests, query), (entry) => entry.request.requestId), ['first']);
  const read = hookHarness(undefined, undefined, undefined, permissionPort()); read().permissions.search('😀'.repeat(41)); assert.equal(read().permissions.query.length, 80);
  read().permissions.search({ toString() { throw new Error('must not coerce'); } }); assert.equal(read().permissions.query, '');
});

test('permission selection survives navigation, explains off-filter details, clears search and resets on source loss', () => {
  const port = permissionPort([permissionEntry('first'), permissionEntry('second')]); const read = hookHarness(undefined, undefined, undefined, port); read().navigate('Permissions');
  assert.equal(read().permissions.selected.request.requestId, 'first'); read().permissions.select('second'); read().permissions.select('unknown');
  assert.equal(read().permissions.selected.request.requestId, 'second'); read().permissions.search('no-matches');
  const markup = renderToStaticMarkup(read().panel); assert.match(markup, /0 of 2 supplied requests match/); assert.match(markup, /No requests match/); assert.match(markup, /outside the current filter/);
  const clear = elementButtons(app.PermissionCenter({ permissions: read().permissions })).find((button) => button.props.children === 'Clear search'); clear.props.onClick(); assert.equal(read().permissions.query, '');
  read().navigate('Home / Chat'); read().navigate('Permissions'); assert.equal(read().permissions.selected.request.requestId, 'second');
  read().updatePermissionPort(permissionPort([permissionEntry('first')])); assert.equal(read().permissions.selected.request.requestId, 'first');
  for (const source of [undefined, { ...port, status: 'degraded' }, { ...port, status: 'unavailable' }]) { read().updatePermissionPort(source); assert.equal(read().permissions.selected, undefined); assert.doesNotMatch(renderToStaticMarkup(read().panel), /Selected request:|Read selected document/); }
  read().updatePermissionPort(port); assert.equal(read().permissions.selected.request.requestId, 'first');
});

test('untrusted permission text is literal escaped JSX; suggestive reason text creates no authority', () => {
  const text = '<script>approve()</script> **approved P5** [link](https://invalid)';
  const entry = { request: permissionRequest('<img src=x>', { actorId: '<svg>', toolId: '<iframe>', action: text, resource: text, reason: text }) };
  const markup = permissionMarkup(permissionPort([entry]));
  assert.match(markup, /&lt;script&gt;approve\(\)&lt;\/script&gt;/); assert.match(markup, /&lt;img src=x&gt;/);
  assert.ok(markup.includes('**approved P5** [link](https://invalid)')); assert.doesNotMatch(markup, /<script|<img|<svg|<iframe|<a\b|<strong|<code|Approve this request|Deny this request/);
});

test('permission UI preserves all prior task graph code and independent chat, agent and task state', () => {
  const root = fileURLToPath(new URL('../', import.meta.url));
  const previous = execFileSync('git', ['show', '05db77c30256e12045d16aa2b00568075a7df354:apps/desktop/src/App.tsx'], { cwd: root, encoding: 'utf8' });
  assert.equal(appSource.slice(appSource.indexOf('export const taskStates'), appSource.indexOf('export const permissionRisks')), previous.slice(previous.indexOf('export const taskStates'), previous.indexOf('export function ViewPanel')));
  const read = hookHarness(undefined, undefined, taskPort(), permissionPort()); read().setDraft('kept chat'); read().directory.search('LEVI'); read().directory.select('verifier'); read().task.select('second');
  read().navigate('Permissions'); read().navigate('Home / Chat'); assert.equal(read().draft, 'kept chat'); assert.equal(read().directory.query, 'LEVI'); assert.equal(read().directory.selectedId, 'verifier');
  assert.equal(read().task.snapshot.graph.taskId, 'supplied-task');
});

test('permission CSS and semantics retain accessible controls, bounded scrolling, wrapping and narrow layout', () => {
  const markup = permissionMarkup(permissionPort(undefined, async () => {}));
  assert.match(markup, /<label for="permission-query">Search permission evidence/); assert.match(markup, /<input[^>]*type="search"[^>]*maxLength="80"/);
  assert.match(markup, /aria-pressed="true" aria-controls="permission-detail"/); assert.match(markup, /role="status" aria-live="polite" aria-atomic="true"/);
  assert.match(css, /\.permission-layout\s*\{[^}]*minmax\(0, 1fr\) minmax\(0, 1fr\)/);
  assert.match(css, /\.permission-results\s*\{[^}]*max-height:[^}]*overflow-y:\s*auto/);
  assert.match(css, /\.permission-text\s*\{[^}]*white-space:\s*pre-wrap;[^}]*overflow-wrap:\s*anywhere/);
  assert.match(css, /@media \(max-width: 1000px\)[\s\S]*?\.permission-layout \{ grid-template-columns: minmax\(0, 1fr\)/);
  assert.match(css, /\.permission-card\s*\{[^}]*min-height:\s*42px/);
  assert.doesNotMatch(css, /\.permission-[^}]*position:\s*(?:fixed|absolute)/);
  assert.doesNotMatch(appSource.slice(appSource.indexOf('export const permissionRisks'), appSource.indexOf('export function ViewPanel')), /UserPermissionDecision|PermissionGrant|createGrant|evaluatePolicy|AuditTrail|ToolRegistry|localStorage|sessionStorage|Date\.|setTimeout|setInterval/);
});

const memoryRecord = (id = 'memory-one', extra = {}) => ({ id, type: 'semantic', ...extra });
const fileRecord = (id = 'file-one', extra = {}) => ({ id, kind: 'source_document', name: 'Supplied document', ...extra });
const memoryEvidence = (records = [memoryRecord()], status = 'connected') => ({ status, records });
const fileEvidence = (items = [fileRecord()], status = 'connected') => ({ status, items });
const evidenceCases = [
  { label: 'memory', read: app.readMemoryPort, make: memoryRecord, port: memoryEvidence, key: 'records', view: app.MemoryWorkspace, prop: 'memory', title: 'Memory' },
  { label: 'files', read: app.readFilePort, make: fileRecord, port: fileEvidence, key: 'items', view: app.FilesWorkspace, prop: 'files', title: 'Files / Documents' },
];
function evidenceMarkup(spec, port, query = '', selectedId) {
  const snapshot = spec.read(port);
  const browser = { snapshot, query, selected: snapshot.entries.find((entry) => entry.id === selectedId) ?? snapshot.entries[0], search() {}, select() {} };
  return renderToStaticMarkup(createElement(spec.view, { [spec.prop]: browser }));
}

for (const spec of evidenceCases) {
  test(`${spec.label} is honest when absent, empty, unavailable, degraded or malformed`, () => {
    assert.equal(spec.read(undefined).rejected, false);
    for (const status of ['connected', 'unavailable', 'degraded']) {
      const snapshot = spec.read(spec.port([], status));
      assert.equal(snapshot.status, status); assert.equal(snapshot.rejected, false); assert.equal(snapshot.entries.length, 0);
      assert.match(evidenceMarkup(spec, spec.port([], status)), /No (memory records|file metadata) supplied/);
      if (status !== 'connected') assert.equal(spec.read(spec.port([spec.make()], status)).entries.length, 0);
      assert.equal(spec.read(spec.port([spec.make('valid'), spec.make('bad', { unexpected: 'private' })], status)).rejected, true);
    }
    for (const value of [null, 1, 'connected', {}, [], { status: 'online', [spec.key]: [] }, { status: 'connected', [spec.key]: [], callback() {} }]) {
      assert.equal(spec.read(value).rejected, true); assert.equal(spec.read(value).entries.length, 0);
    }
    assert.match(evidenceMarkup(spec, null), /Snapshot rejected/);
    assert.doesNotMatch(evidenceMarkup(spec, undefined), /Snapshot rejected/);
  });

  test(`${spec.label} detaches and freezes exact primitive evidence without mutating caller input`, () => {
    const record = spec.make('stable'); const port = spec.port([record]); const before = JSON.stringify(port);
    const snapshot = spec.read(port);
    assert.ok(Object.isFrozen(snapshot)); assert.ok(Object.isFrozen(snapshot.entries)); assert.ok(Object.isFrozen(snapshot.entries[0]));
    assert.notEqual(snapshot.entries, port[spec.key]); assert.notEqual(snapshot.entries[0], record);
    assert.equal(JSON.stringify(port), before); record.id = 'changed'; port[spec.key].push(spec.make('new'));
    assert.equal(snapshot.entries[0].id, 'stable'); assert.equal(snapshot.entries.length, 1);
    assert.ok(Object.isFrozen(spec.read(undefined).entries));
  });

  test(`${spec.label} accepts 64 records in source order and rejects duplicates or overflow whole`, () => {
    const entries = Array.from({ length: 64 }, (_, i) => spec.make(`id-${63 - i}`));
    assert.deepEqual(Array.from(spec.read(spec.port(entries)).entries, (entry) => entry.id), entries.map((entry) => entry.id));
    for (const invalid of [[...entries, spec.make('extra')], [spec.make('duplicate'), spec.make('duplicate')]]) {
      assert.equal(spec.read(spec.port(invalid)).rejected, true); assert.equal(spec.read(spec.port(invalid)).entries.length, 0);
    }
  });

  test(`${spec.label} rejects accessor, symbol, inherited and sparse fields without executing getters`, () => {
    let reads = 0;
    const getter = Object.defineProperty(spec.make(), 'id', { get() { reads++; throw new Error('secret'); } });
    const symbol = { ...spec.make(), [Symbol('private')]: 1 };
    const inherited = Object.create(spec.make());
    const sparse = new Array(2); sparse[0] = spec.make();
    const arrayGetter = [spec.make()]; Object.defineProperty(arrayGetter, '0', { get() { reads++; throw new Error('secret'); } });
    const arrayExtra = [spec.make()]; arrayExtra.extra = 'secret';
    const topGetter = Object.defineProperty({ status: 'connected' }, spec.key, { get() { reads++; throw new Error('secret'); } });
    for (const port of [spec.port([getter]), spec.port([symbol]), spec.port([inherited]), spec.port(sparse), spec.port(arrayGetter), spec.port(arrayExtra), topGetter]) {
      assert.equal(spec.read(port).rejected, true); assert.equal(spec.read(port).entries.length, 0);
    }
    assert.equal(reads, 0);
  });

  test(`${spec.label} handles throwing reflection without coercion or diagnostic leakage`, () => {
    const secret = 'PRIVATE_PROXY_PAYLOAD'; let coercions = 0;
    const hostile = { toString() { coercions++; throw new Error(secret); }, valueOf() { coercions++; throw new Error(secret); } };
    for (const value of [hostile, new Proxy({}, { ownKeys() { throw new Error(secret); } }), new Proxy(spec.port(), { getOwnPropertyDescriptor() { throw new Error(secret); } }), spec.port([new Proxy(spec.make(), { ownKeys() { throw new Error(secret); } })]), spec.port([spec.make(hostile)])]) {
      assert.equal(spec.read(value).rejected, true); assert.doesNotMatch(evidenceMarkup(spec, value), new RegExp(secret));
    }
    assert.equal(coercions, 0);
    // Catching reflection exceptions is robustness in this realm, not a Proxy sandbox.
  });

  test(`${spec.label} IDs are normalized meaningful opaque strings bounded in UTF-16 units`, () => {
    for (const id of ['x'.repeat(128), '😀'.repeat(64), '<b>opaque</b>']) assert.equal(spec.read(spec.port([spec.make(id)])).rejected, false);
    for (const id of ['', ' ', ' padded', 'padded ', 'x'.repeat(129), '😀'.repeat(65), 1, null, undefined, {}]) assert.equal(spec.read(spec.port([spec.make('fixture', { id })])).rejected, true);
  });

  test(`${spec.label} local search preserves source order and does not alter selection or evidence`, () => {
    const snapshot = spec.read(spec.port([spec.make('zeta'), spec.make('alpha'), spec.make('omega')])); const before = JSON.stringify(snapshot);
    assert.deepEqual(Array.from(app.filterEvidence(snapshot.entries, 'A'), (entry) => entry.id), ['zeta', 'alpha', 'omega']);
    assert.equal(app.filterEvidence(snapshot.entries, 'missing').length, 0);
    assert.equal(app.filterEvidence(snapshot.entries, { toString() { throw new Error('never'); } }).length, 3);
    assert.equal(JSON.stringify(snapshot), before);
    assert.match(evidenceMarkup(spec, spec.port([spec.make('first'), spec.make('second')]), 'second', 'first'), /outside the current filter/);
    assert.match(evidenceMarkup(spec, spec.port([spec.make()]), 'no-match'), /0 of 1 supplied items match/);
  });

  test(`${spec.label} uses labelled native search and selection with inert escaped detail text`, () => {
    const payload = '<script>alert(1)</script> [link](javascript:bad)';
    const record = spec.label === 'memory' ? spec.make('opaque', { content: payload, sourceRef: 'javascript:bad' }) : spec.make('opaque', { name: payload, sourceRef: 'javascript:bad' });
    const markup = evidenceMarkup(spec, spec.port([record]));
    assert.match(markup, /<label for="(?:memory|file)-query">Search/); assert.match(markup, /type="search" maxLength="80"/);
    assert.match(markup, /aria-pressed="true" aria-controls="(?:memory|file)-detail"/);
    assert.match(markup, /role="status" aria-live="polite" aria-atomic="true"/);
    assert.match(markup, /&lt;script&gt;/); assert.match(markup, /javascript:bad/);
    assert.doesNotMatch(markup, /<script|<a\b|href=|<iframe|<img|<form|<input[^>]*type="file"/);
    assert.equal((markup.match(/<button\b/g) ?? []).length, 2);
    assert.match(markup, /Not reported/);
  });

  test(`${spec.label} actual local handlers preserve query, select safely, and clear on disconnect`, () => {
    const read = hookHarness(undefined, undefined, undefined, undefined, spec.label === 'memory' ? spec.port([spec.make('first'), spec.make('second')]) : undefined, spec.label === 'files' ? spec.port([spec.make('first'), spec.make('second')]) : undefined);
    read().navigate(spec.title); const browser = () => read()[spec.prop];
    browser().select('second'); assert.equal(browser().selected.id, 'second');
    for (const id of ['unknown', {}, null]) browser().select(id);
    assert.equal(browser().selected.id, 'second'); browser().search('😀'.repeat(41)); assert.equal(browser().query.length, 80);
    browser().search('first'); assert.equal(browser().selected.id, 'second');
    const tree = spec.view({ [spec.prop]: browser() }); const search = tree.props.children[1]; const controls = search.type(search.props);
    const input = controls.props.children[2].props.children[0]; input.props.onChange({ currentTarget: { value: 'second' } }); assert.equal(browser().query, 'second');
    controls.props.children[2].props.children[1].props.onClick(); assert.equal(browser().query, '');
    const cards = elementButtons(spec.view({ [spec.prop]: browser() })).filter((button) => button.props.className === 'evidence-card'); cards[0].props.onClick(); assert.equal(browser().selected.id, 'first');
    const update = spec.label === 'memory' ? 'updateMemoryPort' : 'updateFilePort';
    read()[update](spec.port([spec.make('replacement')])); assert.equal(browser().selected.id, 'replacement');
    read()[update](spec.port([spec.make('replacement')], 'degraded')); assert.equal(browser().selected, undefined);
    read()[update](spec.port([spec.make('fresh')])); assert.equal(browser().selected.id, 'fresh');
    read().navigate('Home / Chat'); read().navigate(spec.title); assert.equal(browser().selected.id, 'fresh');
  });
}

test('memory presentation accepts only the six canonical classes and never fabricates missing evidence', () => {
  assert.deepEqual(Array.from(app.memoryClasses), ['working', 'episodic', 'semantic', 'user', 'project', 'agent']);
  for (const type of app.memoryClasses) assert.equal(app.readMemoryPort(memoryEvidence([memoryRecord('id', { type })])).rejected, false);
  for (const type of ['Semantic', 'other', 'semantic ', null, 1]) assert.equal(app.readMemoryPort(memoryEvidence([memoryRecord('id', { type })])).rejected, true);
  const markup = evidenceMarkup(evidenceCases[0], memoryEvidence());
  assert.equal((markup.match(/<dd[^>]*>Not reported<\/dd>/g) ?? []).length, 11);
  assert.match(markup, /PUDDING owns memory/); assert.match(markup, /source truth, persistence or retrieval/);
});

test('memory text fields enforce confirmed caps, normalization and exact optional presence', () => {
  for (const [field, cap] of [['content', 4000], ['sourceType', 128], ['sourceRef', 512], ['scope', 256], ['embeddingRef', 128]]) {
    const admit = (value) => app.readMemoryPort(memoryEvidence([memoryRecord('id', { [field]: value })])).rejected;
    assert.equal(admit('x'.repeat(cap)), false); assert.equal(admit('x'.repeat(cap + 1)), true);
    for (const value of ['', ' ', null, undefined, 1, {}, []]) assert.equal(admit(value), true, field);
    assert.equal(admit(' padded '), field !== 'content');
  }
  for (const field of ['metadata', 'bytes', 'delete', 'admitted', 'verified', 'callback']) assert.equal(app.readMemoryPort(memoryEvidence([memoryRecord('id', { [field]: true })])).rejected, true);
});

test('memory scores preserve zero and enforce finite numeric values in the closed unit interval', () => {
  for (const field of ['confidence', 'importance']) {
    for (const value of [0, 0.5, 1]) assert.equal(app.readMemoryPort(memoryEvidence([memoryRecord('id', { [field]: value })])).entries[0][field], value);
    for (const value of [-0.1, 1.1, NaN, Infinity, '0', null, undefined, {}]) assert.equal(app.readMemoryPort(memoryEvidence([memoryRecord('id', { [field]: value })])).rejected, true);
  }
  assert.match(evidenceMarkup(evidenceCases[0], memoryEvidence([memoryRecord('id', { confidence: 0, importance: 0 })])), /Supplied confidence<\/dt><dd>0<\/dd>/);
});

test('memory UTC evidence rejects invalid calendars, offsets, caps and chronology without inferring expiry', () => {
  const earlier = '2026-10-09T10:00:00.009Z'; const later = '2026-10-09T10:00:00.01+00:00';
  for (const field of ['createdAt', 'updatedAt', 'lastAccessedAt', 'expiresAt']) {
    for (const value of [earlier, later, '2024-02-29T00:00:00Z']) assert.equal(app.readMemoryPort(memoryEvidence([memoryRecord('id', { [field]: value })])).rejected, false);
    for (const value of ['2025-02-29T00:00:00Z', '2026-10-09T24:00:00Z', '2026-10-09T10:00:00+01:00', '2026-10-09', '2026-10-09T10:00:00.' + '1'.repeat(44) + 'Z', null, undefined, 1]) assert.equal(app.readMemoryPort(memoryEvidence([memoryRecord('id', { [field]: value })])).rejected, true);
  }
  for (const field of ['updatedAt', 'lastAccessedAt', 'expiresAt']) {
    assert.equal(app.readMemoryPort(memoryEvidence([memoryRecord('id', { createdAt: later, [field]: earlier })])).rejected, true);
    assert.equal(app.readMemoryPort(memoryEvidence([memoryRecord('id', { createdAt: earlier, [field]: later })])).rejected, false);
    assert.equal(app.readMemoryPort(memoryEvidence([memoryRecord('id', { createdAt: '2026-10-09T10:00:00Z', [field]: '2026-10-09T10:00:00.000+00:00' })])).rejected, false);
  }
  assert.match(evidenceMarkup(evidenceCases[0], memoryEvidence([memoryRecord('id', { expiresAt: '2000-01-01T00:00:00Z' })])), /no expiry status inferred/);
});

test('files distinguish source documents and generated artifacts without claiming persistence', () => {
  assert.deepEqual(Array.from(app.fileKinds), ['source_document', 'generated_artifact']);
  for (const kind of app.fileKinds) assert.equal(app.readFilePort(fileEvidence([fileRecord('id', { kind })])).rejected, false);
  for (const kind of ['file', 'artifact', 'Generated_artifact', null, 1]) assert.equal(app.readFilePort(fileEvidence([fileRecord('id', { kind })])).rejected, true);
  const markup = evidenceMarkup(evidenceCases[1], fileEvidence([fileRecord('id', { kind: 'generated_artifact' })]));
  assert.match(markup, /Generated artifact/); assert.match(markup, /may exist only in memory/); assert.match(markup, /not proof that it was saved to disk/);
  assert.equal((markup.match(/<dd[^>]*>Not reported<\/dd>/g) ?? []).length, 7);
});

test('file text metadata enforces caps while rejecting bytes, callbacks, storage claims and arbitrary metadata', () => {
  for (const [field, cap] of [['name', 256], ['format', 128], ['mediaType', 128], ['sourceType', 128], ['sourceRef', 512], ['contentHash', 128]]) {
    const admit = (value) => app.readFilePort(fileEvidence([fileRecord('id', { [field]: value })])).rejected;
    assert.equal(admit('x'.repeat(cap)), false); assert.equal(admit('x'.repeat(cap + 1)), true); assert.equal(admit(' padded '), false);
    for (const value of ['', ' ', null, undefined, 1, {}, []]) assert.equal(admit(value), true);
  }
  for (const field of ['metadata', 'bytes', 'content', 'open', 'save', 'delete', 'exists', 'indexed']) assert.equal(app.readFilePort(fileEvidence([fileRecord('id', { [field]: true })])).rejected, true);
});

test('file size and timestamp preserve zero and UTC text without claiming verified hashes', () => {
  for (const sizeBytes of [0, 123, Number.MAX_SAFE_INTEGER]) assert.equal(app.readFilePort(fileEvidence([fileRecord('id', { sizeBytes })])).entries[0].sizeBytes, sizeBytes);
  for (const sizeBytes of [-1, 0.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1, '0', null, undefined]) assert.equal(app.readFilePort(fileEvidence([fileRecord('id', { sizeBytes })])).rejected, true);
  for (const timestamp of ['2026-10-09T10:00:00Z', '2026-10-09T10:00:00.12+00:00']) assert.equal(app.readFilePort(fileEvidence([fileRecord('id', { timestamp })])).entries[0].timestamp, timestamp);
  for (const timestamp of ['2026-02-30T00:00:00Z', '2026-10-09T10:00:00-01:00', undefined]) assert.equal(app.readFilePort(fileEvidence([fileRecord('id', { timestamp })])).rejected, true);
  const markup = evidenceMarkup(evidenceCases[1], fileEvidence([fileRecord('id', { sizeBytes: 0, contentHash: 'supplied-hash' })]));
  assert.match(markup, /Supplied byte size<\/dt><dd>0<\/dd>/); assert.match(markup, /not independently verified/);
});

test('local evidence search covers every admitted metadata field and bounds query without coercion', () => {
  const memory = memoryRecord('id', { content: 'needle', sourceType: 'chat', sourceRef: 'source', scope: 'scope', confidence: 0.125, importance: 0.875, createdAt: '2026-10-09T00:00:00Z', updatedAt: '2026-10-09T00:00:01Z', lastAccessedAt: '2026-10-09T00:00:02Z', expiresAt: '2026-10-10T00:00:00Z', embeddingRef: 'embedding' });
  const file = fileRecord('file', { format: 'markdown', mediaType: 'text/plain', sourceType: 'upload', sourceRef: 'file-source', contentHash: 'hash', timestamp: '2026-10-09T00:00:00Z', sizeBytes: 987 });
  for (const [read, port, record] of [[app.readMemoryPort, memoryEvidence, memory], [app.readFilePort, fileEvidence, file]]) {
    const entries = read(port([record])).entries;
    for (const value of Object.values(record)) assert.equal(app.filterEvidence(entries, String(value).toUpperCase()).length, 1);
    assert.equal(app.filterEvidence(entries, '').length, 1);
  }
  const long = app.readMemoryPort(memoryEvidence([memoryRecord('x'.repeat(80))])).entries;
  assert.equal(app.filterEvidence(long, 'x'.repeat(80) + 'ignored').length, 1);
});

test('both evidence workspaces preserve earlier logic byte for byte and keep all workspace states independent', () => {
  const root = fileURLToPath(new URL('../', import.meta.url));
  const previous = execFileSync('git', ['show', '5594229ace471804e4571fc40bc159d4b020862e:apps/desktop/src/App.tsx'], { cwd: root, encoding: 'utf8' });
  assert.equal(appSource.slice(0, appSource.indexOf('export const memoryClasses')), previous.slice(0, previous.indexOf('export function ViewPanel')));
  const read = hookHarness(undefined, undefined, taskPort(), permissionPort(), memoryEvidence([memoryRecord('first'), memoryRecord('second')]), fileEvidence([fileRecord('one'), fileRecord('two')]));
  read().setDraft('kept'); read().directory.search('LEVI'); read().task.select('second'); read().permissions.search('permission');
  read().memory.select('second'); read().memory.search('first'); read().files.select('two'); read().files.search('one');
  for (const [, label] of expectedViews) read().navigate(label);
  assert.equal(read().draft, 'kept'); assert.equal(read().directory.query, 'LEVI'); assert.equal(read().permissions.query, 'permission');
  assert.equal(read().memory.selected.id, 'second'); assert.equal(read().memory.query, 'first'); assert.equal(read().files.selected.id, 'two'); assert.equal(read().files.query, 'one');
});

test('evidence CSS retains focus, responsive wrapping, bounded scrolling and reachable native controls', () => {
  assert.match(css, /\.evidence-layout\s*\{[^}]*minmax\(0, 1fr\) minmax\(0, 1fr\)/);
  assert.match(css, /\.evidence-results\s*\{[^}]*max-height:[^}]*overflow-y:\s*auto/);
  assert.match(css, /\.evidence-text\s*\{[^}]*white-space:\s*pre-wrap;[^}]*overflow-wrap:\s*anywhere/);
  assert.match(css, /\.evidence-card\s*\{[^}]*min-height:\s*42px/);
  assert.match(css, /@media \(max-width: 1000px\)[\s\S]*?\.evidence-layout \{ grid-template-columns: minmax\(0, 1fr\)/);
  assert.match(css, /:focus-visible/); assert.doesNotMatch(css, /\.evidence-[^}]*position:\s*(?:fixed|absolute)/);
  const block = appSource.slice(appSource.indexOf('export const memoryClasses'), appSource.indexOf('export function ViewPanel'));
  assert.doesNotMatch(block, /Date\.|setTimeout|setInterval|port\.[a-zA-Z]+\(|\.submit\(|\.sendText\(/);
});

const modelEntry = (extra = {}) => ({ providerId: 'provider-one', modelId: 'model-one', ...extra });
const voiceComponent = (extra = {}) => ({ id: 'stt', status: 'available', ...extra });
const modelEvidence = (entries = [modelEntry()], status = 'connected') => ({ status, entries });
const voiceEvidence = (components = [voiceComponent()], status = 'connected', extra = {}) => ({ status, components, ...extra });
const runtimeCases = [
  { name: 'models', read: app.readModelPort, make: modelEntry, port: modelEvidence, key: 'entries', limit: 64, view: app.ModelsWorkspace, prop: 'models', title: 'Models', update: 'updateModelPort' },
  { name: 'voice', read: app.readVoicePort, make: voiceComponent, port: voiceEvidence, key: 'components', limit: 6, view: app.VoiceWorkspace, prop: 'voice', title: 'Voice', update: 'updateVoicePort' },
];
function runtimeMarkup(spec, port, query = '', selection) {
  const snapshot = spec.read(port);
  const browser = { snapshot, query, selected: snapshot.entries.find((entry) => app.runtimeKey(entry) === selection) ?? snapshot.entries[0], search() {}, select() {} };
  return renderToStaticMarkup(createElement(spec.view, { [spec.prop]: browser }));
}

for (const spec of runtimeCases) {
  test(`${spec.name} admits only exact whole data snapshots and suppresses disconnected details`, () => {
    assert.equal(spec.read(undefined).rejected, false);
    for (const value of [null, [], 1, '', {}, { status: 'connected' }, { status: 'online', [spec.key]: [] }, { ...spec.port(), callback() {} }, { ...spec.port(), [Symbol('extra')]: 1 }]) assert.equal(spec.read(value).rejected, true);
    for (const status of ['connected', 'unavailable', 'degraded']) {
      assert.equal(spec.read(spec.port([], status)).rejected, false);
      assert.equal(spec.read(spec.port([spec.make()], status)).entries.length, status === 'connected' ? 1 : 0);
      assert.equal(spec.read(spec.port([spec.make(), spec.make({ forbidden: 'private' })], status)).rejected, true);
      assert.equal(spec.read(spec.port([spec.make(), spec.make({ forbidden: 'private' })], status)).entries.length, 0);
    }
    assert.match(runtimeMarkup(spec, undefined), /No (model|voice) evidence source connected/);
    assert.match(runtimeMarkup(spec, null), /Snapshot rejected/);
    assert.doesNotMatch(runtimeMarkup(spec, undefined), /Snapshot rejected/);
    for (const field of spec.name === 'models' ? ['providerId', 'modelId'] : ['id', 'status']) {
      const record = spec.make(); delete record[field]; assert.equal(spec.read(spec.port([record])).rejected, true);
    }
  });

  test(`${spec.name} admits the exact count bound, rejects duplicate/oversized lists and preserves order`, () => {
    const entries = spec.name === 'models' ? Array.from({ length: 64 }, (_, i) => modelEntry({ modelId: `model-${63 - i}` })) : Array.from(app.voiceComponentIds, (id) => voiceComponent({ id }));
    const snapshot = spec.read(spec.port(entries)); assert.equal(snapshot.entries.length, spec.limit);
    assert.deepEqual(Array.from(snapshot.entries, (entry) => app.runtimeKey(entry)), entries.map((entry) => app.runtimeKey(entry)));
    for (const invalid of [[...entries, spec.make()], [spec.make(), spec.make()]]) assert.equal(spec.read(spec.port(invalid)).rejected, true);
  });

  test(`${spec.name} detaches frozen records and arrays without changing caller data`, () => {
    const port = spec.port(); const before = JSON.stringify(port); const snapshot = spec.read(port);
    assert.equal(JSON.stringify(port), before); assert.ok(Object.isFrozen(snapshot)); assert.ok(Object.isFrozen(snapshot.entries)); assert.ok(Object.isFrozen(snapshot.entries[0]));
    assert.notEqual(snapshot.entries[0], port[spec.key][0]); assert.notEqual(snapshot.entries, port[spec.key]);
    port[spec.key][0][spec.name === 'models' ? 'modelId' : 'id'] = 'changed'; port[spec.key].push(spec.make());
    assert.equal(snapshot.entries.length, 1); assert.notEqual(snapshot.entries[0][spec.name === 'models' ? 'modelId' : 'id'], 'changed');
    assert.ok(Object.isFrozen(spec.read(undefined).entries));
  });

  test(`${spec.name} rejects sparse/extended/symbol/accessor/inherited data without reading getters`, () => {
    let reads = 0; const field = spec.name === 'models' ? 'modelId' : 'id';
    const getter = Object.defineProperty(spec.make(), field, { get() { reads++; throw new Error('private'); } });
    const recordSymbol = { ...spec.make(), [Symbol('extra')]: 1 };
    const sparse = new Array(2); sparse[0] = spec.make(); const extended = [spec.make()]; extended.extra = true;
    const arrayGetter = [spec.make()]; Object.defineProperty(arrayGetter, '0', { get() { reads++; throw new Error('private'); } });
    const topGetter = Object.defineProperty({ status: 'connected' }, spec.key, { get() { reads++; throw new Error('private'); } });
    for (const value of [spec.port([getter]), spec.port([recordSymbol]), spec.port([Object.create(spec.make())]), spec.port(sparse), spec.port(extended), spec.port(arrayGetter), topGetter]) assert.equal(spec.read(value).rejected, true);
    assert.equal(reads, 0);
  });

  test(`${spec.name} catches reflection failures and rejects coercible objects without leaking diagnostics`, () => {
    const secret = 'PRIVATE_RUNTIME_PROXY'; let coerced = 0;
    const hostile = { toString() { coerced++; throw new Error(secret); }, valueOf() { coerced++; throw new Error(secret); } };
    for (const value of [hostile, new Proxy({}, { ownKeys() { throw new Error(secret); } }), new Proxy(spec.port(), { getOwnPropertyDescriptor() { throw new Error(secret); } }), spec.port([new Proxy(spec.make(), { ownKeys() { throw new Error(secret); } })]), spec.port([spec.make({ [spec.name === 'models' ? 'modelId' : 'id']: hostile })])]) {
      assert.equal(spec.read(value).rejected, true); assert.doesNotMatch(runtimeMarkup(spec, value), new RegExp(secret));
    }
    assert.equal(coerced, 0);
  });

  test(`${spec.name} rejects unsafe metadata, operations, credentials and domain payload fields`, () => {
    for (const field of ['metadata', 'url', 'path', 'credentials', 'exception', 'callback', ...(spec.name === 'models' ? ['prompt', 'output', 'loaded', 'generate'] : ['deviceId', 'deviceName', 'microphonePermission', 'pcm', 'transcript', 'audio', 'voiceProfile'])]) {
      assert.equal(spec.read(spec.port([spec.make({ [field]: true })])).rejected, true);
      assert.equal(spec.read({ ...spec.port(), [field]: true }).rejected, true);
    }
  });

  test(`${spec.name} local search and inspection handlers retain independent navigation state and reset on loss`, () => {
    const entries = spec.name === 'models' ? [modelEntry(), modelEntry({ modelId: 'second' })] : [voiceComponent(), voiceComponent({ id: 'tts' })];
    const read = hookHarness(undefined, undefined, undefined, undefined, undefined, undefined, spec.name === 'models' ? spec.port(entries) : undefined, spec.name === 'voice' ? spec.port(entries) : undefined);
    const browser = () => read()[spec.prop]; read().navigate(spec.title);
    browser().select(app.runtimeKey(entries[1])); assert.equal(app.runtimeKey(browser().selected), app.runtimeKey(entries[1]));
    for (const value of ['unknown', undefined, {}]) browser().select(value);
    assert.equal(app.runtimeKey(browser().selected), app.runtimeKey(entries[1]));
    browser().search('😀'.repeat(41)); assert.equal(browser().query.length, 80); browser().search(spec.name === 'models' ? 'model-one' : 'stt');
    assert.match(runtimeMarkup(spec, spec.port(entries), browser().query, app.runtimeKey(entries[1])), /outside the current filter/);
    read().navigate('Settings'); read().navigate(spec.title); assert.equal(app.runtimeKey(browser().selected), app.runtimeKey(entries[1]));
    const tree = spec.view({ [spec.prop]: browser() }); const search = tree.props.children[1]; const controls = search.type(search.props);
    controls.props.children[2].props.children[0].props.onChange({ currentTarget: { value: 'nothing' } }); assert.equal(browser().query, 'nothing');
    controls.props.children[2].props.children[1].props.onClick(); assert.equal(browser().query, '');
    elementButtons(spec.view({ [spec.prop]: browser() })).filter((button) => button.props.className === 'runtime-inspect')[0].props.onClick(); assert.equal(app.runtimeKey(browser().selected), app.runtimeKey(entries[0]));
    read()[spec.update](spec.port([entries[1]])); assert.equal(app.runtimeKey(browser().selected), app.runtimeKey(entries[1]));
    for (const status of ['degraded', 'unavailable']) { read()[spec.update](spec.port(entries, status)); assert.equal(browser().selected, undefined); }
    read()[spec.update](spec.port(entries)); assert.equal(app.runtimeKey(browser().selected), app.runtimeKey(entries[0]));
    read()[spec.update](null); assert.equal(browser().selected, undefined);
  });

  test(`${spec.name} has labelled native local controls, truthful notices and passive evidence cards`, () => {
    const markup = runtimeMarkup(spec, spec.port());
    assert.match(markup, /type="search" maxLength="80"/); assert.match(markup, /<label for="(?:model|voice)-query">Search/);
    assert.match(markup, /role="status" aria-live="polite" aria-atomic="true"/); assert.match(markup, /<article class="runtime-card">/);
    assert.match(markup, /aria-pressed="true" aria-controls="(?:model|voice)-detail"/); assert.match(markup, /Inspect evidence/);
    assert.doesNotMatch(markup, /<button[^>]*class="runtime-card"|<a\b|href=|<iframe|<img|<script|type="file"/);
    assert.match(runtimeMarkup(spec, spec.port(), 'missing'), /0 of 1 supplied records match/);
    assert.match(runtimeMarkup(spec, spec.port([], 'connected')), /0 of 0 supplied records match/);
  });
}

test('model identities are bounded original strings and composite pairs never collapse across providers or delimiters', () => {
  for (const field of ['providerId', 'modelId']) {
    for (const value of ['x'.repeat(128), '😀'.repeat(64), ' padded ']) assert.equal(app.readModelPort(modelEvidence([modelEntry({ [field]: value })])).entries[0][field], value);
    for (const value of ['', ' ', 'x'.repeat(129), '😀'.repeat(65), null, undefined, 1, {}]) assert.equal(app.readModelPort(modelEvidence([modelEntry({ [field]: value })])).rejected, true);
  }
  const entries = [modelEntry({ providerId: 'a:b', modelId: 'c' }), modelEntry({ providerId: 'a', modelId: 'b:c' }), modelEntry({ providerId: 'other', modelId: 'c' })];
  assert.equal(app.readModelPort(modelEvidence(entries)).entries.length, 3); assert.equal(new Set(entries.map(app.runtimeKey)).size, 3);
});

test('model optional booleans and provider statuses use exact types and preserve false', () => {
  assert.deepEqual(Array.from(app.providerStatuses), ['healthy', 'degraded', 'unavailable']);
  for (const providerStatus of app.providerStatuses) assert.equal(app.readModelPort(modelEvidence([modelEntry({ providerStatus })])).rejected, false);
  for (const providerStatus of ['available', 'Healthy', null, undefined, 1]) assert.equal(app.readModelPort(modelEvidence([modelEntry({ providerStatus })])).rejected, true);
  for (const field of ['supportsStreaming', 'supportsStructuredOutput', 'supportsToolUse', 'supportsVision', 'supportsCancellation']) {
    for (const value of [true, false]) assert.equal(app.readModelPort(modelEvidence([modelEntry({ [field]: value })])).entries[0][field], value);
    for (const value of ['true', 'false', 0, 1, null, undefined, {}]) assert.equal(app.readModelPort(modelEvidence([modelEntry({ [field]: value })])).rejected, true);
    assert.match(runtimeMarkup(runtimeCases[0], modelEvidence([modelEntry({ [field]: false })])), /<dd>false<\/dd>/);
  }
  assert.equal((runtimeMarkup(runtimeCases[0], modelEvidence()).match(/<dd>Not reported<\/dd>/g) ?? []).length, 9);
});

test('model context size accepts only positive safe integers without inferring application settings', () => {
  for (const contextLimitTokens of [1, 16384, Number.MAX_SAFE_INTEGER]) assert.equal(app.readModelPort(modelEvidence([modelEntry({ contextLimitTokens })])).entries[0].contextLimitTokens, contextLimitTokens);
  for (const contextLimitTokens of [0, -0, -1, 1.1, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1, '1', true, null, undefined]) assert.equal(app.readModelPort(modelEvidence([modelEntry({ contextLimitTokens })])).rejected, true);
  const markup = runtimeMarkup(runtimeCases[0], modelEvidence([modelEntry({ contextLimitTokens: 1, supportsToolUse: true })]));
  assert.match(markup, /Capability flags confer no permissions/); assert.match(markup, /Neither proves a live provider/);
});

test('all runtime timestamp fields enforce bounded real UTC calendar dates without a clock', () => {
  const valid = ['2024-02-29T00:00:00Z', '2026-10-10T00:00:00+00:00', '2026-10-10T00:00:00.' + '1'.repeat(43) + 'Z'];
  const invalid = ['2025-02-29T00:00:00Z', '2026-04-31T00:00:00Z', '2026-10-10T24:00:00Z', '2026-10-10T00:00:60Z', '2026-10-10T00:00:00+01:00', '2026-10-10', '2026-10-10T00:00:00.' + '1'.repeat(44) + 'Z', null, undefined, 1, {}];
  for (const [spec, field] of [[runtimeCases[0], 'providerCheckedAt'], [runtimeCases[0], 'capabilitiesRefreshedAt'], [runtimeCases[1], 'checkedAt']]) {
    for (const value of valid) assert.equal(spec.read(spec.port([spec.make({ [field]: value })])).entries[0][field], value);
    for (const value of invalid) assert.equal(spec.read(spec.port([spec.make({ [field]: value })])).rejected, true);
  }
});

test('voice exact state and component vocabularies remain caller evidence and suppress stale state', () => {
  assert.deepEqual(Array.from(app.voiceStates), ['sleeping', 'listening', 'thinking', 'speaking', 'degraded_voice', 'text_only']);
  assert.deepEqual(Array.from(app.voiceComponentIds), ['wake_word', 'vad', 'stt', 'tts', 'conversation', 'audio_output']);
  for (const state of app.voiceStates) assert.equal(app.readVoicePort(voiceEvidence([], 'connected', { state })).state, state);
  for (const state of ['ready', 'healthy', 'LISTENING', null, undefined, true]) assert.equal(app.readVoicePort(voiceEvidence([], 'connected', { state })).rejected, true);
  for (const status of ['available', 'degraded', 'unavailable']) assert.equal(app.readVoicePort(voiceEvidence([voiceComponent({ status })])).entries[0].status, status);
  for (const field of ['id', 'status']) for (const value of ['', 'unknown', 'Available', null, undefined, 1]) assert.equal(app.readVoicePort(voiceEvidence([voiceComponent({ [field]: value })])).rejected, true);
  for (const status of ['degraded', 'unavailable']) {
    const port = voiceEvidence(undefined, status, { state: 'speaking' }); assert.equal(app.readVoicePort(port).state, undefined);
    assert.doesNotMatch(runtimeMarkup(runtimeCases[1], port), /<dd>speaking<\/dd>|Status \(supplied\): available/);
    assert.equal(app.readVoicePort(voiceEvidence([], status, { state: 'invalid' })).rejected, true);
  }
  assert.match(runtimeMarkup(runtimeCases[1], voiceEvidence()), /not whole-application health/);
  assert.match(runtimeMarkup(runtimeCases[1], voiceEvidence()), /Text interaction remains available/);
  assert.equal((runtimeMarkup(runtimeCases[1], voiceEvidence()).match(/<dd>Not reported<\/dd>/g) ?? []).length, 2);
});

test('runtime search covers supplied numeric and boolean evidence in source order with bounded inert input', () => {
  const entries = app.readModelPort(modelEvidence([modelEntry({ modelId: 'z', supportsVision: false, contextLimitTokens: 987 }), modelEntry({ modelId: 'a', supportsVision: true })])).entries;
  assert.deepEqual(Array.from(app.filterRuntime(entries, 'PROVIDER'), (entry) => entry.modelId), ['z', 'a']);
  assert.equal(app.filterRuntime(entries, 'FALSE')[0].modelId, 'z'); assert.equal(app.filterRuntime(entries, 'true')[0].modelId, 'a'); assert.equal(app.filterRuntime(entries, '987')[0].modelId, 'z');
  assert.equal(app.filterRuntime(entries, { toString() { throw new Error('never'); } }).length, 2);
  const long = app.readModelPort(modelEvidence([modelEntry({ modelId: 'x'.repeat(80) })])).entries; assert.equal(app.filterRuntime(long, 'x'.repeat(80) + 'ignored').length, 1);
});

test('model identity strings remain escaped inert text without provider or file links', () => {
  const markup = runtimeMarkup(runtimeCases[0], modelEvidence([modelEntry({ providerId: '<script>alert(1)</script>', modelId: '[link](javascript:bad)' })]));
  assert.match(markup, /&lt;script&gt;/); assert.match(markup, /javascript:bad/); assert.doesNotMatch(markup, /<script|<a\b|href=|<img|<iframe/);
});

test('Settings is a semantic non-editor without a port, fabricated values, callbacks or controls', () => {
  const markup = renderToStaticMarkup(createElement(app.SettingsWorkspace));
  for (const label of ['Model / provider configuration', 'Voice / device configuration', 'Application preferences', 'Privacy and permissions', 'Storage / integration']) assert.ok(markup.includes(label));
  assert.match(markup, /Settings cannot be changed or saved/); assert.match(markup, /No current settings values are reported/); assert.match(markup, /PermissionEngine alone authorizes/);
  assert.doesNotMatch(markup, /<(button|input|select|textarea|a|form)\b|role="switch"|disabled=|Settings saved|Settings applied/);
  assert.doesNotMatch(appSource, /SettingsPort/); assert.match(renderToStaticMarkup(createElement(app.ViewPanel, { view: app.resolveView('settings') })), /Settings overview/);
});

test('PHX-086 preserves every prior logic block and PHX-085 tests byte for byte while state stays independent', () => {
  const root = fileURLToPath(new URL('../', import.meta.url));
  const previous = execFileSync('git', ['show', '66cfbfdff7a4a0f94312641cf1bdcc2c682b5061:apps/desktop/src/App.tsx'], { cwd: root, encoding: 'utf8' });
  assert.equal(appSource.slice(0, appSource.indexOf('export const providerStatuses')), previous.slice(0, previous.indexOf('export function ViewPanel')));
  const baselineTests = execFileSync('git', ['show', '66cfbfdff7a4a0f94312641cf1bdcc2c682b5061:tests/workspace.test.mjs'], { cwd: root, encoding: 'utf8' });
  const currentTests = readFileSync(new URL('./workspace.test.mjs', import.meta.url), 'utf8');
  assert.equal(currentTests.slice(currentTests.indexOf('const memoryRecord'), currentTests.indexOf('const modelEntry')).trim(), baselineTests.slice(baselineTests.indexOf('const memoryRecord')).trim());
  const read = hookHarness(undefined, undefined, taskPort(), permissionPort(), memoryEvidence(), fileEvidence(), modelEvidence(), voiceEvidence());
  read().setDraft('kept'); read().directory.search('LEVI'); read().memory.search('memory'); read().files.search('file'); read().permissions.search('request'); read().models.search('model'); read().voice.search('stt');
  for (const [, label] of expectedViews) read().navigate(label);
  assert.equal(read().draft, 'kept'); assert.equal(read().directory.query, 'LEVI'); assert.equal(read().memory.query, 'memory'); assert.equal(read().files.query, 'file'); assert.equal(read().permissions.query, 'request'); assert.equal(read().models.query, 'model'); assert.equal(read().voice.query, 'stt');
});

test('runtime CSS keeps cards passive and lists responsive, scrollable, wrapped and keyboard reachable', () => {
  assert.match(css, /\.runtime-layout\s*\{[^}]*minmax\(0, 1fr\) minmax\(0, 1fr\)/); assert.match(css, /\.runtime-results\s*\{[^}]*max-height:[^}]*overflow-y:\s*auto/);
  assert.match(css, /\.runtime-header dd, \.runtime-detail dd\s*\{[^}]*white-space:\s*pre-wrap;[^}]*overflow-wrap:\s*anywhere/);
  assert.match(css, /\.runtime-search button, \.runtime-inspect\s*\{[^}]*min-height:\s*42px/); assert.match(css, /@media \(max-width: 1000px\)[\s\S]*?\.runtime-layout, \.settings-overview \{ grid-template-columns: minmax\(0, 1fr\)/);
  assert.match(css, /:focus-visible/); assert.match(css, /prefers-reduced-motion/); assert.doesNotMatch(css, /\.runtime-card[^}]*cursor:\s*pointer|\.runtime-card:hover|\.runtime-[^}]*position:\s*(?:fixed|absolute)/);
});

const diagnosticTime = '2026-10-10T12:30:45.123Z';
const diagnosticMetric = (id = 'metric-first', extra = {}) => ({ id, name: 'ttft', value: 0, ...extra });
const diagnosticLog = (id = 'log-first', extra = {}) => ({ id, timestamp: diagnosticTime, severity: 'info', subsystem: 'core', message: 'Supplied observation', ...extra });
const diagnosticTrace = (id = 'trace-first', extra = {}) => ({ id, timestamp: diagnosticTime, kind: 'decision', summary: 'Supplied decision evidence', ...extra });
const diagnosticEvidence = (extra = {}) => ({ status: 'connected', metrics: [diagnosticMetric()], logs: [diagnosticLog()], traces: [diagnosticTrace()], ...extra });
const diagnosticCases = [
  { key: 'metrics', make: diagnosticMetric, required: ['id', 'name', 'value'], optional: ['unit', 'recordedAt'], extras: { unit: 'ms', recordedAt: diagnosticTime } },
  { key: 'logs', make: diagnosticLog, required: ['id', 'timestamp', 'severity', 'subsystem', 'message'], optional: ['correlationId', 'taskId', 'stepId', 'errorType'], extras: { correlationId: 'correlation', taskId: 'task', stepId: 'step', errorType: 'reported_error' } },
  { key: 'traces', make: diagnosticTrace, required: ['id', 'timestamp', 'kind', 'summary'], optional: ['correlationId', 'taskId', 'stepId', 'actorId'], extras: { correlationId: 'correlation', taskId: 'task', stepId: 'step', actorId: 'verifier' } },
];
function diagnosticMarkup(port, query = '', selection) {
  const snapshot = app.readObservabilityPort(port);
  const diagnostics = { snapshot };
  for (const { key } of diagnosticCases) diagnostics[key] = { entries: snapshot[key], query, selected: snapshot[key].find((entry) => entry.id === selection) ?? snapshot[key][0], search() {}, select() {} };
  return renderToStaticMarkup(createElement(app.ObservabilityWorkspace, { diagnostics }));
}
function diagnosticRejected(value) {
  const snapshot = app.readObservabilityPort(value);
  assert.equal(snapshot.rejected, true);
  for (const { key } of diagnosticCases) assert.equal(snapshot[key].length, 0, key);
  return snapshot;
}

test('PHX-087 defaults disconnected and distinguishes supplied empty, unavailable, degraded and rejected sources', () => {
  assert.match(diagnosticMarkup(undefined), /No diagnostic source connected/);
  assert.equal(app.readObservabilityPort(undefined).rejected, false);
  const read = hookHarness(); read().navigate('Observability / Developer');
  assert.match(renderToStaticMarkup(read().panel), /No diagnostic source connected/);
  const empty = diagnosticMarkup(diagnosticEvidence({ metrics: [], logs: [], traces: [] }));
  assert.match(empty, /Live telemetry is not connected/);
  for (const { key } of diagnosticCases) assert.ok(empty.includes(`connected source supplied no ${key}`));
  assert.match(empty, /Empty evidence does not establish runtime inactivity/);
  for (const status of ['unavailable', 'degraded']) {
    const port = diagnosticEvidence({ status }); const snapshot = app.readObservabilityPort(port);
    assert.equal(snapshot.rejected, false); assert.equal(snapshot.missing, false);
    assert.match(diagnosticMarkup(port), new RegExp(`Diagnostic source ${status}`));
    for (const { key } of diagnosticCases) assert.equal(snapshot[key].length, 0);
    assert.doesNotMatch(diagnosticMarkup(port), /metric-first|log-first|trace-first|Supplied observation/);
    diagnosticRejected(diagnosticEvidence({ status, logs: [diagnosticLog('bad', { timestamp: 'invalid' })] }));
  }
  for (const value of [null, false, 1, '', [], {}, diagnosticEvidence({ status: 'healthy' }), diagnosticEvidence({ extra: 1 })]) diagnosticRejected(value);
});

test('PHX-087 presentation types expose only the exact approved own data fields', () => {
  const fields = (name) => nodes(ts.isTypeAliasDeclaration).find((node) => node.name.text === name).type.members.map((member) => member.name.text);
  assert.deepEqual(fields('ObservabilityPort'), ['status', 'metrics', 'logs', 'traces']);
  for (const [index, name] of ['MetricDisplayEntry', 'LogDisplayEntry', 'TraceDisplayEntry'].entries()) assert.deepEqual(fields(name), [...diagnosticCases[index].required, ...diagnosticCases[index].optional]);
  for (const key of ['status', 'metrics', 'logs', 'traces']) { const port = diagnosticEvidence(); delete port[key]; diagnosticRejected(port); }
  for (const key of ['status', 'metrics', 'logs', 'traces']) diagnosticRejected(diagnosticEvidence({ [key]: undefined }));
});

for (const spec of diagnosticCases) {
  test(`PHX-087 ${spec.key} require exact fields, optional primitives and whole-snapshot admission`, () => {
    const port = diagnosticEvidence({ [spec.key]: [spec.make('complete', spec.extras)] });
    const snapshot = app.readObservabilityPort(port); assert.equal(snapshot.rejected, false);
    assert.equal(JSON.stringify(snapshot[spec.key]), JSON.stringify(port[spec.key]));
    assert.ok(diagnosticMarkup(port).includes('complete'));
    for (const key of spec.required) { const row = spec.make(); delete row[key]; diagnosticRejected(diagnosticEvidence({ [spec.key]: [spec.make('good'), row] })); }
    for (const key of [...spec.required, ...spec.optional]) {
      for (const invalid of [undefined, null, {}, [], () => 1, true]) diagnosticRejected(diagnosticEvidence({ [spec.key]: [spec.make('bad', { [key]: invalid })] }));
    }
    for (const key of ['extra', 'metadata', 'stack', 'prompt', 'modelOutput', 'transcript', 'body', 'credentials', 'environment', 'path', 'arguments', 'result', 'callback']) diagnosticRejected(diagnosticEvidence({ [spec.key]: [spec.make('bad', { [key]: 'forbidden field' })] }));
    const invalidMarkup = diagnosticMarkup(diagnosticEvidence({ [spec.key]: [spec.make('valid-but-hidden'), spec.make('invalid', { extra: 'private diagnostic' })] }));
    assert.match(invalidMarkup, /Diagnostic snapshot rejected/); assert.doesNotMatch(invalidMarkup, /valid-but-hidden|private diagnostic|metric-first|log-first|trace-first/);
  });
  test(`PHX-087 ${spec.key} enforce 64 unique IDs, strict normalized identities and source order`, () => {
    const rows = Array.from({ length: 64 }, (_, index) => spec.make(`id-${63 - index}`));
    const snapshot = app.readObservabilityPort(diagnosticEvidence({ [spec.key]: rows }));
    assert.equal(snapshot.rejected, false); assert.equal(JSON.stringify(snapshot[spec.key].map((row) => row.id)), JSON.stringify(rows.map((row) => row.id)));
    diagnosticRejected(diagnosticEvidence({ [spec.key]: [...rows, spec.make('overflow')] }));
    diagnosticRejected(diagnosticEvidence({ [spec.key]: [spec.make('same'), spec.make('same')] }));
    for (const id of [' ', '\t', ' id', 'id ', 'x'.repeat(129), '😀'.repeat(65)]) diagnosticRejected(diagnosticEvidence({ [spec.key]: [spec.make(id)] }));
    assert.equal(app.readObservabilityPort(diagnosticEvidence({ [spec.key]: [spec.make('😀'.repeat(64))] })).rejected, false);
  });
  test(`PHX-087 ${spec.key} reject sparse, extended, accessor and reflective arrays without exposing input`, () => {
    let reads = 0;
    const accessor = []; Object.defineProperty(accessor, '0', { get() { reads++; throw Error('private reflection'); } });
    const extended = [spec.make()]; extended.extra = 1;
    const symbol = [spec.make()]; symbol[Symbol('hidden')] = 1;
    const sparse = [spec.make(), , spec.make('last')];
    const traps = [new Proxy([spec.make()], { ownKeys() { throw Error('private reflection'); } }), new Proxy([spec.make()], { getOwnPropertyDescriptor() { throw Error('private reflection'); } })];
    for (const array of [null, {}, 'array', sparse, accessor, extended, symbol, ...traps]) diagnosticRejected(diagnosticEvidence({ [spec.key]: array }));
    assert.equal(reads, 0);
  });
  test(`PHX-087 ${spec.key} read only own descriptors, detach values and freeze every admitted level`, () => {
    let reads = 0;
    const accessor = spec.make(); Object.defineProperty(accessor, 'id', { get() { reads++; throw Error('private read'); } });
    const symbolic = spec.make(); symbolic[Symbol('private')] = 1;
    for (const row of [accessor, symbolic, new Proxy(spec.make(), { ownKeys() { throw Error('private read'); } }), new Proxy(spec.make(), { getOwnPropertyDescriptor() { throw Error('private read'); } })]) diagnosticRejected(diagnosticEvidence({ [spec.key]: [row] }));
    const row = spec.make('preserved', spec.extras);
    const proxy = new Proxy(row, { get() { reads++; throw Error('private read'); } });
    const port = diagnosticEvidence({ [spec.key]: [proxy] }); const snapshot = app.readObservabilityPort(port);
    assert.equal(snapshot.rejected, false); assert.equal(reads, 0);
    assert.ok(Object.isFrozen(snapshot)); for (const { key } of diagnosticCases) { assert.ok(Object.isFrozen(snapshot[key])); for (const entry of snapshot[key]) assert.ok(Object.isFrozen(entry)); }
    row.id = 'changed'; port[spec.key].push(spec.make('appended'));
    assert.equal(snapshot[spec.key].length, 1); assert.equal(snapshot[spec.key][0].id, 'preserved');
  });
}

test('PHX-087 top-level reflection rejects safely and cross-collection IDs remain independent', () => {
  let reads = 0; const accessor = diagnosticEvidence(); Object.defineProperty(accessor, 'status', { get() { reads++; throw Error('private source'); } });
  const symbolic = diagnosticEvidence(); symbolic[Symbol('hidden')] = true;
  for (const port of [accessor, symbolic, new Proxy(diagnosticEvidence(), { ownKeys() { throw Error('private source'); } }), new Proxy(diagnosticEvidence(), { getOwnPropertyDescriptor() { throw Error('private source'); } })]) { diagnosticRejected(port); assert.doesNotMatch(diagnosticMarkup(port), /private source/); }
  assert.equal(reads, 0);
  const same = diagnosticEvidence({ metrics: [diagnosticMetric('same')], logs: [diagnosticLog('same')], traces: [diagnosticTrace('same')] });
  assert.equal(app.readObservabilityPort(same).rejected, false);
});

test('PHX-087 admits every exact diagnostic vocabulary and rejects invented values', () => {
  const names = ['task_step_latency', 'provider_model', 'ttft', 'tokens_per_sec', 'ram', 'vram', 'cpu', 'disk_io', 'tool_success_failure', 'verification_result', 'recovery_attempts', 'permission_decisions'];
  const severities = ['debug', 'info', 'warning', 'error', 'critical'];
  const kinds = ['decision', 'agent', 'tool', 'permission', 'failure', 'recovery', 'verification'];
  assert.deepEqual(Array.from(app.diagnosticMetricNames), names); assert.deepEqual(Array.from(app.diagnosticSeverities), severities); assert.deepEqual(Array.from(app.diagnosticTraceKinds), kinds);
  for (const [key, field, values, make] of [['metrics', 'name', names, diagnosticMetric], ['logs', 'severity', severities, diagnosticLog], ['traces', 'kind', kinds, diagnosticTrace]]) {
    for (const value of values) assert.equal(app.readObservabilityPort(diagnosticEvidence({ [key]: [make('valid', { [field]: value })] })).rejected, false);
    for (const value of ['unknown', values[0].toUpperCase(), ` ${values[0]}`, 1, { toString() { throw Error('coercion'); } }]) diagnosticRejected(diagnosticEvidence({ [key]: [make('bad', { [field]: value })] }));
  }
});

for (const [key, field, bound, normalized] of [
  ['metrics', 'id', 128, true], ['metrics', 'value', 256, false], ['metrics', 'unit', 32, false],
  ['logs', 'id', 128, true], ['logs', 'subsystem', 128, false], ['logs', 'message', 1000, false], ['logs', 'errorType', 128, false],
  ['traces', 'id', 128, true], ['traces', 'summary', 1000, false],
  ...['logs', 'traces'].flatMap((key) => ['correlationId', 'taskId', 'stepId'].map((field) => [key, field, 128, true])), ['traces', 'actorId', 128, true],
]) {
  test(`PHX-087 ${key}.${field} enforces its ${bound} UTF-16 bound without repairing text`, () => {
    const spec = diagnosticCases.find((spec) => spec.key === key);
    for (const text of ['x'.repeat(bound), '😀'.repeat(bound / 2)]) assert.equal(app.readObservabilityPort(diagnosticEvidence({ [key]: [spec.make('valid', { [field]: text })] })).rejected, false);
    for (const text of ['', ' \n\t', 'x'.repeat(bound + 1), '😀'.repeat(bound / 2) + 'x']) diagnosticRejected(diagnosticEvidence({ [key]: [spec.make('bad', { [field]: text })] }));
    const text = '  exact text\n'; const snapshot = app.readObservabilityPort(diagnosticEvidence({ [key]: [spec.make('valid', { [field]: text })] }));
    if (normalized) assert.equal(snapshot.rejected, true); else { assert.equal(snapshot.rejected, false); assert.equal(snapshot[key][0][field], text); }
  });
}

test('PHX-087 metric values preserve finite numeric primitives and strings without aggregation or units', () => {
  for (const value of [0, -0, -123.5, Number.MAX_VALUE, Number.MIN_VALUE, '0', ' 123.50 ']) {
    const snapshot = app.readObservabilityPort(diagnosticEvidence({ metrics: [diagnosticMetric('value', { value })] }));
    assert.equal(snapshot.rejected, false); assert.ok(Object.is(snapshot.metrics[0].value, value));
    const markup = diagnosticMarkup(diagnosticEvidence({ metrics: [diagnosticMetric('value', { value })] }));
    assert.ok(markup.includes(`<dd>${typeof value}</dd>`)); assert.match(markup, /Unit \(supplied\)<\/dt><dd>Not reported/);
  }
  for (const value of [NaN, Infinity, -Infinity, true, null, undefined, 1n, { valueOf() { throw Error('coercion'); } }]) diagnosticRejected(diagnosticEvidence({ metrics: [diagnosticMetric('bad', { value })] }));
});

test('PHX-087 every diagnostic timestamp requires bounded real calendar UTC without clock inference', () => {
  for (const [key, field, make] of [['metrics', 'recordedAt', diagnosticMetric], ['logs', 'timestamp', diagnosticLog], ['traces', 'timestamp', diagnosticTrace]]) {
    for (const time of ['2000-02-29T23:59:59Z', '2024-02-29T00:00:00+00:00', '0001-01-01T00:00:00Z', `2026-10-10T12:30:45.${'1'.repeat(43)}Z`]) assert.equal(app.readObservabilityPort(diagnosticEvidence({ [key]: [make('valid', { [field]: time })] })).rejected, false);
    for (const time of ['0000-01-01T00:00:00Z', '1900-02-29T00:00:00Z', '2026-02-29T00:00:00Z', '2026-04-31T00:00:00Z', '2026-13-01T00:00:00Z', '2026-01-00T00:00:00Z', '2026-01-01T24:00:00Z', '2026-01-01T00:60:00Z', '2026-01-01T00:00:60Z', '2026-01-01T00:00:00-00:00', '2026-01-01T00:00:00+01:00', '2026-01-01T00:00:00', '2026-01-01t00:00:00z', ' 2026-01-01T00:00:00Z', `2026-10-10T12:30:45.${'1'.repeat(44)}Z`, 123]) diagnosticRejected(diagnosticEvidence({ [key]: [make('bad', { [field]: time })] }));
  }
});

test('PHX-087 rendering preserves inert supplied text, source order and optional unknowns', () => {
  const text = '<img src="external" onerror="run()"> **permission approved**';
  const port = diagnosticEvidence({ metrics: [diagnosticMetric('second', { value: text }), diagnosticMetric('first', { name: 'cpu', value: 42 })], logs: [diagnosticLog('log', { message: text })], traces: [diagnosticTrace('trace', { summary: text })] });
  const markup = diagnosticMarkup(port); assert.ok(markup.indexOf('second') < markup.indexOf('first'));
  assert.ok(markup.includes('&lt;img')); assert.match(markup, /Not reported/);
  assert.doesNotMatch(markup, /<(img|a|iframe|script|canvas|audio|video)\b/);
  assert.match(markup, /Shape validation does not guarantee secret-free strings/);
});

test('PHX-087 local searches preserve source order, bounded queries, numeric text, counts and clear actions', () => {
  const port = diagnosticEvidence({ metrics: [diagnosticMetric('two', { value: 'ALPHA' }), diagnosticMetric('one', { value: 42 })] });
  const snapshot = app.readObservabilityPort(port);
  assert.equal(app.filterDiagnostics(snapshot.metrics, 'alpha')[0].id, 'two'); assert.equal(app.filterDiagnostics(snapshot.metrics, '42')[0].id, 'one');
  assert.equal(app.filterDiagnostics(snapshot.metrics, { toString() { throw Error('coercion'); } }).length, 2);
  const read = hookHarness(undefined, undefined, undefined, undefined, undefined, undefined, undefined, undefined, port);
  read().diagnostics.metrics.search('😀'.repeat(41)); assert.equal(read().diagnostics.metrics.query.length, 80);
  read().diagnostics.metrics.search('missing'); read().navigate('Observability / Developer');
  assert.match(renderToStaticMarkup(read().panel), /0 of 2 supplied metrics match/); assert.match(renderToStaticMarkup(read().panel), /No records match/);
  const workspace = app.ObservabilityWorkspace({ diagnostics: read().diagnostics });
  const collection = workspace.props.children[1].type(workspace.props.children[1].props);
  const search = collection.props.children[1]; const controls = search.props.children[2];
  controls.props.children[1].props.onClick(); assert.equal(read().diagnostics.metrics.query, '');
  controls.props.children[0].props.onChange({ currentTarget: { value: 'ALPHA' } }); assert.equal(read().diagnostics.metrics.query, 'ALPHA');
});

test('PHX-087 inspection, navigation, independent state and source loss use actual local handlers', () => {
  const port = diagnosticEvidence({ metrics: [diagnosticMetric('m1'), diagnosticMetric('m2')], logs: [diagnosticLog('l1'), diagnosticLog('l2')], traces: [diagnosticTrace('t1'), diagnosticTrace('t2')] });
  const read = hookHarness(undefined, undefined, taskPort(), permissionPort(), memoryEvidence(), fileEvidence(), modelEvidence(), voiceEvidence(), port);
  read().setDraft('retained'); read().directory.search('LEVI'); read().memory.search('memory'); read().models.search('model');
  for (const [index, spec] of diagnosticCases.entries()) { read().diagnostics[spec.key].search(`query-${index}`); read().diagnostics[spec.key].select(port[spec.key][1].id); }
  for (const [, label] of expectedViews) read().navigate(label);
  for (const [index, spec] of diagnosticCases.entries()) { assert.equal(read().diagnostics[spec.key].query, `query-${index}`); assert.equal(read().diagnostics[spec.key].selected.id, port[spec.key][1].id); }
  assert.equal(read().draft, 'retained'); assert.equal(read().directory.query, 'LEVI'); assert.equal(read().memory.query, 'memory'); assert.equal(read().models.query, 'model');
  read().diagnostics.metrics.select('unknown'); assert.equal(read().diagnostics.metrics.selected.id, 'm2');
  read().diagnostics.metrics.search('');
  const workspace = app.ObservabilityWorkspace({ diagnostics: read().diagnostics }); const collection = workspace.props.children[1].type(workspace.props.children[1].props);
  collection.props.children[2].props.children[0].props.children.props.children[0].props.children.props.children[3].props.onClick();
  assert.equal(read().diagnostics.metrics.selected.id, 'm1');
  for (const status of ['unavailable', 'degraded']) { read().updateObservabilityPort({ ...port, status }); for (const { key } of diagnosticCases) assert.equal(read().diagnostics[key].selected, undefined); assert.doesNotMatch(renderToStaticMarkup(read().panel), /m1|l1|t1/); }
  read().updateObservabilityPort(undefined); for (const { key } of diagnosticCases) assert.equal(read().diagnostics[key].selected, undefined);
  read().updateObservabilityPort(port); for (const { key } of diagnosticCases) assert.equal(read().diagnostics[key].selected.id, port[key][0].id);
  read().diagnostics.logs.select('l2'); read().updateObservabilityPort({ ...port, logs: [diagnosticLog('new')] }); assert.equal(read().diagnostics.logs.selected.id, 'new');
  assert.equal(read().diagnostics.metrics.selected.id, 'm1'); assert.equal(read().diagnostics.traces.selected.id, 't1');
});

test('PHX-087 preserves all prior implementation, runtime tests and security surfaces', () => {
  const base = 'b2cf0fa3552d430343e06b813e76eb50f5b62e04'; const root = fileURLToPath(new URL('../', import.meta.url));
  const before = execFileSync('git', ['show', `${base}:apps/desktop/src/App.tsx`], { cwd: root, encoding: 'utf8' });
  assert.equal(appSource.slice(0, appSource.indexOf('export const diagnosticMetricNames')), before.slice(0, before.indexOf('export function ViewPanel')));
  const beforeTests = execFileSync('git', ['show', `${base}:tests/workspace.test.mjs`], { cwd: root, encoding: 'utf8' });
  const currentTests = readFileSync(new URL('./workspace.test.mjs', import.meta.url), 'utf8');
  assert.equal(currentTests.slice(currentTests.indexOf('const modelEntry'), currentTests.indexOf('const diagnosticTime')).trim(), beforeTests.slice(beforeTests.indexOf('const modelEntry')).trim());
  const beforeCss = execFileSync('git', ['show', `${base}:apps/desktop/src/styles.css`], { cwd: root, encoding: 'utf8' }); assert.ok(css.startsWith(beforeCss));
  assert.doesNotMatch(appSource, /console\.|clipboard|writeFile|telemetry\(|collectLogs|fetch\(|WebSocket|setInterval|setTimeout|localStorage|sessionStorage/);
});

test('PHX-087 diagnostic CSS and markup keep passive cards, semantic controls and reachable responsive lists', () => {
  assert.match(css, /\.diagnostic-results\s*\{[^}]*max-height:[^}]*overflow-y:\s*auto/);
  assert.match(css, /\.diagnostic-detail dd\s*\{[^}]*white-space:\s*pre-wrap;[^}]*overflow-wrap:\s*anywhere/);
  assert.match(css, /@media \(max-width: 1000px\)[\s\S]*\.diagnostic-layout \{ grid-template-columns: minmax\(0, 1fr\)/);
  assert.match(css, /\.diagnostic-search-controls button, \.diagnostic-inspect\s*\{[^}]*min-height:\s*42px/);
  assert.doesNotMatch(css, /\.diagnostic-card[^}]*cursor:\s*pointer|\.diagnostic-card:hover|\.diagnostic-[^}]*position:\s*(fixed|absolute)/);
  const markup = diagnosticMarkup(diagnosticEvidence());
  for (const label of ['Diagnostic source status', 'Metrics', 'Structured logs', 'Trace events', 'Search supplied metrics', 'Search supplied logs', 'Search supplied traces']) assert.ok(markup.includes(label));
  assert.match(markup, /aria-live="polite"/); assert.match(markup, /type="search"/); assert.match(markup, /type="button"/);
  assert.doesNotMatch(markup, /<article[^>]*onClick|<(a|form|iframe)\b/);
});

test('PHX-087 suppressed sources report counts unavailable rather than claiming empty supplied collections', () => {
  for (const port of [undefined, diagnosticEvidence({ status: 'unavailable' }), diagnosticEvidence({ status: 'degraded' }), diagnosticEvidence({ extra: 1 })]) {
    const markup = diagnosticMarkup(port);
    assert.equal((markup.match(/Diagnostic counts unavailable while details are hidden\./g) ?? []).length, 3);
    assert.doesNotMatch(markup, /0 of 0 supplied|connected source supplied no|metric-first|log-first|trace-first/);
  }
  const empty = diagnosticMarkup(diagnosticEvidence({ metrics: [], logs: [], traces: [] }));
  assert.equal((empty.match(/0 of 0 supplied/g) ?? []).length, 3);
  assert.doesNotMatch(empty, /Diagnostic counts unavailable/);
});

test('PHX-108 the native proxy allowlist is exactly the twenty documented REST routes', () => {
  const proxy = readFileSync(new URL('../apps/desktop/src-tauri/src/proxy.rs', import.meta.url), 'utf8');
  const table = proxy.slice(proxy.indexOf('pub const ROUTES'), proxy.indexOf('];', proxy.indexOf('pub const ROUTES')));
  const routes = [...table.matchAll(/Method::(Get|Post|Patch),\s*"([^"]+)",\s*&\[([^\]]*)\]/g)]
    .map(([, method, path, keys]) => `${method.toUpperCase()} ${path} [${keys.replace(/\s+/g, '')}]`);
  assert.deepEqual(routes, [
    'GET /health []', 'GET /ready []', 'POST /chat []', 'GET /missions ["conversation_id","limit","offset"]', 'GET /missions/{id} []',
    'POST /missions/{id}/cancel []', 'POST /missions/{id}/run-again []', 'GET /permissions ["limit","offset"]', 'GET /permissions/{id} []',
    'POST /permissions/{id}/approve []', 'POST /permissions/{id}/deny []', 'GET /grants ["include_inactive","limit","offset"]',
    'GET /conversations ["limit","offset"]', 'GET /conversations/{id}/messages ["limit","offset"]', 'GET /agents []', 'GET /models []',
    'GET /settings []', 'PATCH /settings []',
    // PHX-108 (ADR-110, ADR-112): read-only setup routes, no query and no body.
    'GET /onboarding []', 'GET /capabilities []',
  ]);
  // Every fixed route the adapters name is on the allowlist (destructive routes stay absent).
  const core = readFileSync(new URL('../apps/desktop/src/adapters/core.ts', import.meta.url), 'utf8');
  const named = [...core.matchAll(/(?:request\('(GET|POST|PATCH)', |this\.get\()[`']([^`'?$]+)[`'?]/g)].map(([, method, path]) => `${method ?? 'GET'} ${path}`);
  for (const route of ['GET /onboarding', 'GET /capabilities', 'PATCH /settings', 'GET /settings']) assert.ok(named.includes(route), route);
  for (const route of named) assert.ok(routes.some((entry) => entry.startsWith(`${route} `)), route);
});

// PHX-106/107 (ADR-091, ADR-092): adapter, transport and native-boundary tests run in this file's node:test run.
import './desktop_adapters.mjs';
