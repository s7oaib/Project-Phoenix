// PHX-106/107 (ADR-091, ADR-092): adapter, transport and native-boundary tests.
// Imported by workspace.test.mjs so `npm test` and the PHX-090 matrix run them as one file.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createContext, runInContext } from 'node:vm';
import * as React from 'react';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const source = (path) => readFileSync(new URL(`../apps/desktop/src/${path}`, import.meta.url), 'utf8');
const nodeRequire = createRequire(import.meta.url);

// A tiny CommonJS loader for the desktop TypeScript sources, with injectable externals. All
// modules share one realm (as in the webview), so App's `instanceof Promise` acknowledgement
// checks see the adapters' native Promises. Values crossing into node are compared as plain JSON.
const plain = (value) => JSON.parse(JSON.stringify(value));
function loader(externals = {}) {
  const cache = new Map();
  const realm = createContext({ console, setImmediate });
  function load(path) {
    if (cache.has(path)) return cache.get(path).exports;
    const module = { exports: {} };
    cache.set(path, module);
    const { outputText } = ts.transpileModule(source(path), {
      compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    });
    const dir = path.includes('/') ? path.slice(0, path.lastIndexOf('/') + 1) : '';
    const require = (name) => {
      if (name in externals) return externals[name];
      if (name.endsWith('.css')) return {};
      if (name === 'react') return React;
      if (name.startsWith('.')) {
        const parts = `${dir}${name}`.split('/');
        const resolved = [];
        for (const part of parts) {
          if (part === '..') resolved.pop(); else if (part !== '.') resolved.push(part);
        }
        const base = resolved.join('/');
        for (const candidate of [`${base}.ts`, `${base}.tsx`]) {
          try { source(candidate); return load(candidate); } catch (error) { if (error.code !== 'ENOENT') throw error; }
        }
        throw new Error(`cannot resolve ${name}`);
      }
      return nodeRequire(name);
    };
    runInContext(`(function (module, exports, require) {${outputText}\n})`, realm)(module, module.exports, require);
    return module.exports;
  }
  return load;
}

const load = loader();
const app = load('App.tsx');
const core = load('adapters/core.ts');

const MISSION = '11111111-1111-4111-8111-111111111111';
const CONV = '22222222-2222-4222-8222-222222222222';
const REQ = '33333333-3333-4333-8333-333333333333';
const RERUN = '44444444-4444-4444-8444-444444444444';
const TASK = '55555555-5555-4555-8555-555555555555';
const MSG1 = '66666666-6666-4666-8666-666666666666';
const MSG2 = '77777777-7777-4777-8777-777777777777';
const AT = '2026-10-18T10:00:00.123456Z';

function page(items, total = items.length) { return { items, limit: 64, offset: 0, total }; }
function detail(overrides = {}) {
  return { mission_id: MISSION, conversation_id: CONV, status: 'waiting_permission', path: 'complex', failure_code: null,
    responder_error: null, rerun_of: null, created_at: AT, started_at: AT, finished_at: null, request_text: 'Summarize notes.txt',
    answer_text: null, rerun_id: null, task_id: TASK, task_states: ['created', 'planning', 'running', 'waiting_permission'],
    permission_request: { request_id: REQ, mission_id: MISSION, status: 'pending', tool_id: 'filesystem.read_text', action: 'read_text',
      resource: 'D:\\docs\\notes.txt', risk_level: 'P2', requested_at: AT, decided_at: null }, ...overrides };
}
function backendRoutes(overrides = {}) {
  const routes = {
    'GET /settings': { status: 200, body: { workspace: { root: 'D:\\docs', status: 'active', updated_at: AT }, model_tiers: {}, default_tier: [], retention_days: 30, api: { host: '127.0.0.1', port: 5000 } } },
    'GET /models': { status: 200, body: [{ provider_id: 'ollama', model_id: 'qwen3:8b', roles: ['default'], registered: true, provider_status: 'healthy', generating: false, context_limit_tokens: 32768 }] },
    'GET /permissions?limit=64': { status: 200, body: page([detail().permission_request]) },
    'GET /conversations?limit=1': { status: 200, body: page([{ conversation_id: CONV, created_at: AT, updated_at: AT }], 1) },
    'GET /conversations?limit=1&offset=0': { status: 200, body: page([{ conversation_id: CONV, created_at: AT, updated_at: AT }], 1) },
    [`GET /missions?conversation_id=${CONV}&limit=1`]: { status: 200, body: page([{ mission_id: MISSION }], 1) },
    [`GET /missions?conversation_id=${CONV}&limit=1&offset=0`]: { status: 200, body: page([{ mission_id: MISSION }], 1) },
    [`GET /missions/${MISSION}`]: { status: 200, body: detail() },
    [`GET /missions/${RERUN}`]: { status: 200, body: detail({ mission_id: RERUN, status: 'completed', rerun_of: MISSION, permission_request: null, task_states: ['completed'] }) },
    [`GET /conversations/${CONV}/messages?limit=1`]: { status: 200, body: page([], 2) },
    [`GET /conversations/${CONV}/messages?limit=64&offset=0`]: { status: 200, body: page([
      { message_id: MSG1, role: 'user', text: 'Summarize notes.txt', created_at: AT },
      { message_id: MSG2, role: 'phoenix', text: 'notes.txt lists three tasks.', created_at: AT }]) },
    'POST /chat': { status: 202, body: { mission_id: MISSION, conversation_id: CONV, status: 'queued', rerun_of: null } },
    [`POST /permissions/${REQ}/approve`]: { status: 202, body: { request_id: REQ, duration: 'session', superseded_mission_id: MISSION, mission_id: RERUN } },
    [`POST /permissions/${REQ}/deny`]: { status: 200, body: { request_id: REQ, mission_id: MISSION, status: 'failed', failure_code: 'permission_denied' } },
    'PATCH /settings': { status: 422, body: { error: { code: 'workspace_link', message: 'x', request_id: 'r' } } },
    ...overrides,
  };
  return routes;
}
function mockTransport(routes = backendRoutes()) {
  const calls = [];
  const handlers = {};
  return {
    calls, handlers,
    async request(method, path, body) {
      calls.push({ method, path, body });
      return routes[`${method} ${path}`] ?? { status: 404, body: { error: { code: 'not_found', message: 'Not found.', request_id: 'r' } } };
    },
    async backendStatus() { return { state: 'ready' }; },
    async subscribe(next) { Object.assign(handlers, next); return () => { handlers.stopped = true; }; },
  };
}
function manualScheduler() {
  const timers = [];
  return {
    timers,
    setTimeout(callback) { timers.push(callback); return timers.length; },
    clearTimeout() {},
    setInterval() { return 1; },
    clearInterval() {},
    async flush() {
      for (let round = 0; round < 10; round += 1) {
        while (timers.length) timers.shift()();
        for (let tick = 0; tick < 30; tick += 1) await new Promise((resolve) => setImmediate(resolve));
      }
    },
  };
}
async function readyStore(routes) {
  const transport = mockTransport(routes);
  const scheduler = manualScheduler();
  const store = new core.PhoenixStore(transport, scheduler);
  await store.start();
  await scheduler.flush();
  return { store, transport, scheduler };
}
function envelope(eventType, payload, overrides = {}) {
  envelope.count = (envelope.count ?? 0) + 1;
  return { event_id: `e-${envelope.count}`, event_type: eventType, timestamp: AT, task_id: TASK, agent_id: 'phoenix',
    payload, severity: 'info', correlation_id: MISSION, ...overrides };
}

test('PHX-107 every live port snapshot is admitted by the unchanged App readers', async () => {
  const { store, transport } = await readyStore();
  const events = [
    envelope('task.created', { path: 'complex' }),
    envelope('agent.started', { role: 'planner' }, { agent_id: 'strategist' }),
    envelope('agent.completed', { role: 'planner', outcome: 'completed', duration_ms: 900 }, { agent_id: 'strategist' }),
    envelope('step.started', { step_id: 's1', domain: 'documents', final: false }, { agent_id: 'document_analyst' }),
    envelope('agent.started', { role: 'document', step_id: 's1' }, { agent_id: 'document_analyst' }),
    envelope('tool.requested', { step_id: 's1', tool_id: 'filesystem.read_text', call_id: REQ, operation: 'read_text' }, { agent_id: 'document_analyst' }),
    envelope('permission.requested', { step_id: 's1', tool_id: 'filesystem.read_text', call_id: REQ, permission_request_id: REQ, risk_level: 'P2' }, { agent_id: 'document_analyst' }),
    envelope('agent.completed', { role: 'document', step_id: 's1', outcome: 'waiting_permission', duration_ms: 10 }, { agent_id: 'document_analyst' }),
    envelope('step.started', { step_id: 'final', domain: 'general', final: true }, { agent_id: 'phoenix' }),
    envelope('verification.started', { step_id: 'final' }, { agent_id: 'verifier' }),
    envelope('verification.passed', { step_id: 'final', verification_id: RERUN, verdict: 'pass', duration_ms: 50 }, { agent_id: 'verifier' }),
    envelope('step.completed', { step_id: 'final', duration_ms: 1200 }),
    envelope('task.state_changed', { scope: 'task', from_state: 'running', to_state: 'waiting_permission' }),
    envelope('unknown.type', 'not an object'),
  ];
  for (const event of events) transport.handlers.event(event);
  const view = store.getSnapshot();

  const chat = app.readChatPort(view.chatPort);
  assert.equal(chat.status, 'connected');
  assert.deepEqual(plain(app.boundedMessages(chat.messages)), { omitted: false, messages: [
    { id: MSG1, role: 'user', content: 'Summarize notes.txt' }, { id: MSG2, role: 'assistant', content: 'notes.txt lists three tasks.' }] });

  const permissions = app.readPermissionPort(view.permissionPort);
  assert.equal(permissions.rejected, false);
  assert.equal(permissions.status, 'connected');
  assert.equal(permissions.requests.length, 1);
  assert.equal(permissions.requests[0].request.actorId, 'document_analyst');
  assert.equal(permissions.requests[0].request.resource, 'D:\\docs\\notes.txt');
  assert.equal(permissions.requests[0].evaluation.reasonCode, 'p2_approval_required');
  assert.ok(app.permissionNeedsIntent(permissions.requests[0]));
  assert.equal(typeof permissions.submitDecision, 'function');

  const graph = app.readTaskGraphPort(view.taskPort);
  assert.equal(graph.rejected, false);
  assert.equal(graph.graph.taskId, TASK);
  assert.equal(graph.graph.taskState, 'waiting_permission');
  assert.deepEqual(plain(graph.graph.steps.map((step) => [step.stepId, step.ownerAgentId])), [['s1', 'document_analyst'], ['final', 'phoenix']]);
  assert.match(graph.graph.steps[0].objective, /waiting for your permission \(filesystem\.read_text\)/);
  assert.match(graph.graph.steps[1].objective, /completed \(LEVI passed\) in 1200 ms/);

  const agents = app.readAgentSnapshot(view.agentPort);
  assert.equal(agents.rejected, false);
  assert.deepEqual(agents.agents.find((agent) => agent.internalId === 'document_analyst').state, 'waiting_permission');
  assert.equal(agents.agents.find((agent) => agent.internalId === 'strategist').state, 'idle');

  const models = app.readModelPort(view.modelPort);
  assert.equal(models.rejected, false);
  assert.deepEqual(plain(models.entries), [{ providerId: 'ollama', modelId: 'qwen3:8b', providerStatus: 'healthy', contextLimitTokens: 32768 }]);

  const diagnostics = app.readObservabilityPort(view.observabilityPort);
  assert.equal(diagnostics.rejected, false);
  assert.equal(diagnostics.traces.length, events.length - 1);
  assert.deepEqual(plain(diagnostics.metrics.map((metric) => metric.name)), ['verification_result', 'task_step_latency']);
  assert.equal(view.connection.workspace.root, 'D:\\docs');
  assert.equal(view.connection.mission.status, 'waiting_permission');
});

test('PHX-107 submitDecision maps approve and deny to the exact REST calls and follows the re-run', async () => {
  const { store, transport, scheduler } = await readyStore();
  const port = store.getSnapshot().permissionPort;
  transport.calls.length = 0;
  assert.equal(await port.submitDecision(REQ, 'approved'), undefined);
  assert.deepEqual(plain(transport.calls[0]), { method: 'POST', path: `/permissions/${REQ}/approve`, body: { duration: 'session' } });
  await scheduler.flush();
  assert.ok(transport.calls.some((call) => call.path === `/missions/${RERUN}`));
  assert.equal(store.getSnapshot().connection.mission.missionId, RERUN);

  transport.calls.length = 0;
  assert.equal(await port.submitDecision(REQ, 'denied'), undefined);
  assert.deepEqual(plain(transport.calls[0]), { method: 'POST', path: `/permissions/${REQ}/deny`, body: {} });

  // Delivered through App's own intent path, the acknowledgement is a native Promise of undefined.
  const flight = { pending: false, submittedIds: [] };
  const { store: fresh } = await readyStore();
  assert.equal(await app.deliverPermissionIntent(fresh.getSnapshot().permissionPort, REQ, 'approved', flight), 'submitted');

  const { store: refusing, transport: refused } = await readyStore(backendRoutes({ [`POST /permissions/${REQ}/approve`]: { status: 409, body: { error: { code: 'workspace_changed' } } } }));
  await assert.rejects(refusing.getSnapshot().permissionPort.submitDecision(REQ, 'approved'), { code: 'workspace_changed' });
  const before = refused.calls.length;
  await assert.rejects(refusing.getSnapshot().permissionPort.submitDecision('../settings', 'approved'));
  await assert.rejects(refusing.getSnapshot().permissionPort.submitDecision(REQ, 'always'));
  assert.equal(refused.calls.length, before);
});

test('PHX-107 sendText posts trimmed text, keeps the conversation and honours the chosen path', async () => {
  const { store, transport } = await readyStore(backendRoutes({ 'GET /conversations?limit=1': { status: 200, body: page([], 0) } }));
  transport.calls.length = 0;
  assert.equal(await app.sendDraft(store.getSnapshot().chatPort, '  Hello PHOENIX  ', { pending: false, cancelled: false }), 'acknowledged');
  assert.deepEqual(plain(transport.calls[0]), { method: 'POST', path: '/chat', body: { text: 'Hello PHOENIX' } });
  store.setPath('complex');
  await store.getSnapshot().chatPort.sendText('Read notes.txt');
  assert.deepEqual(plain(transport.calls.at(-1).body), { text: 'Read notes.txt', conversation_id: CONV, path: 'complex' });
  const { store: busy } = await readyStore(backendRoutes({ 'POST /chat': { status: 503, body: { error: { code: 'service_unavailable' } } } }));
  assert.equal(await app.sendDraft(busy.getSnapshot().chatPort, 'hi', { pending: false, cancelled: false }), 'failed');
});

test('PHX-107 events and stream reconnects trigger coalesced REST refetches; REST stays the source of truth', async () => {
  const { transport, scheduler } = await readyStore();
  transport.calls.length = 0;
  transport.handlers.event(envelope('task.state_changed', { scope: 'mission', mission_status: 'running' }));
  transport.handlers.event(envelope('task.state_changed', { scope: 'mission', mission_status: 'completed' }));
  assert.equal(scheduler.timers.length, 1); // two events, one coalesced refresh
  await scheduler.flush();
  const paths = transport.calls.map((call) => call.path);
  assert.ok(paths.includes(`/missions/${MISSION}`));
  assert.ok(paths.includes('/permissions?limit=64'));
  assert.ok(paths.includes(`/conversations/${CONV}/messages?limit=64&offset=0`));
  assert.ok(transport.calls.every((call) => call.method === 'GET'));

  transport.calls.length = 0;
  transport.handlers.stream({ connected: false, code: 1013 });
  await scheduler.flush();
  for (const path of ['/settings', '/models', '/permissions?limit=64', `/missions/${MISSION}`]) assert.ok(transport.calls.some((call) => call.path === path), path);

  transport.calls.length = 0;
  transport.handlers.backend({ state: 'degraded', code: 'backend_exited' });
  transport.handlers.event(envelope('permission.requested', { step_id: 's1', permission_request_id: REQ, tool_id: 'filesystem.read_text', call_id: REQ, risk_level: 'P2' }));
  await scheduler.flush();
  assert.equal(transport.calls.length, 0); // nothing is fetched while the backend is down
});

test('PHX-107 backend states map to honest port statuses and degraded codes', async () => {
  const transport = mockTransport();
  transport.backendStatus = async () => ({ state: 'starting' });
  const scheduler = manualScheduler();
  const store = new core.PhoenixStore(transport, scheduler);
  await store.start();
  let view = store.getSnapshot();
  assert.equal(view.chatPort.status, 'unavailable');
  assert.equal(app.readPermissionPort(view.permissionPort).requests.length, 0);
  transport.handlers.backend({ state: 'degraded', code: 'already_running' });
  view = store.getSnapshot();
  assert.equal(view.chatPort.status, 'degraded');
  assert.equal(app.readTaskGraphPort(view.taskPort).graph, undefined);
  transport.handlers.backend({ state: 'bogus' });
  assert.equal(store.getSnapshot().connection.backend.code, 'already_running');
  store.stop();
  assert.equal(transport.handlers.stopped, true);
});

test('PHX-107 long answers become ordered parts and only the latest 64 transcript entries are kept', () => {
  const long = 'a'.repeat(core.MESSAGE_PART) + 'b'.repeat(10);
  const messages = core.toChatMessages(page([{ message_id: MSG1, role: 'phoenix', text: long, created_at: AT }, { message_id: MSG2, role: 'system', text: 'x' }]));
  assert.deepEqual(plain(messages.map((message) => [message.id, message.content.length])), [[`${MSG1}:1`, 4000], [`${MSG1}:2`, 10]]);
  assert.equal(app.boundedMessages(messages).omitted, false);
  const many = Array.from({ length: 70 }, (_, index) => ({ message_id: `${String(index).padStart(8, '0')}-0000-4000-8000-000000000000`, role: 'user', text: `m${index}` }));
  const window = core.toChatMessages(page(many));
  assert.equal(window.length, 64);
  assert.equal(window[0].content, 'm6');
  assert.equal(core.utc('2026-10-18T10:00:00'), '2026-10-18T10:00:00Z');
  assert.equal(core.utc('2026-10-18T10:00:00+00:00'), '2026-10-18T10:00:00Z');
  assert.equal(core.utc('2026-10-18T10:00:00+05:30'), undefined);
});

test('PHX-107 permissions without a P1-P4 risk carry no decision controls', () => {
  const pending = [{ requestId: REQ, missionId: MISSION, toolId: 'filesystem.read_text', action: 'read_text', resource: 'D:\\a', riskLevel: null, requestedAt: AT }];
  for (const riskLevel of [null, 'P0', 'P5', 'P9']) {
    const entries = core.toPermissionEntries([{ ...pending[0], riskLevel }], new Map());
    const admitted = app.readPermissionPort({ status: 'connected', requests: entries });
    assert.equal(admitted.rejected, false);
    assert.equal(app.permissionNeedsIntent(admitted.requests[0]), false, String(riskLevel));
  }
});

test('PHX-107 workspace folder errors are explained by code and never echo the path back', async () => {
  const { store, transport } = await readyStore();
  await store.setWorkspace('  D:\\link  ');
  assert.deepEqual(plain(transport.calls.at(-1)), { method: 'PATCH', path: '/settings', body: { workspace_root: 'D:\\link' } });
  assert.match(store.getSnapshot().connection.workspaceMessage, /Links, junctions/);
  await store.setWorkspace('   ');
  assert.match(store.getSnapshot().connection.workspaceMessage, /absolute folder path/);
});

test('PHX-106/107 tauri transport uses only the two allowlisted commands and three shell events', async () => {
  const invoked = [];
  const listened = [];
  const tauri = loader({
    '@tauri-apps/api/core': { invoke: async (command, args) => { invoked.push([command, args]); return { status: 200, body: null }; }, isTauri: () => true },
    '@tauri-apps/api/event': { listen: async (name) => { listened.push(name); return () => listened.push(`stop:${name}`); } },
  })('adapters/tauri.ts');
  const transport = tauri.createTauriTransport();
  await transport.request('GET', '/ready');
  await transport.request('POST', '/chat', { text: 'hi' });
  await transport.backendStatus();
  const stop = await transport.subscribe({ event() {}, stream() {}, backend() {} });
  stop();
  assert.deepEqual(plain(invoked), [
    ['api_request', { method: 'GET', path: '/ready', body: null }],
    ['api_request', { method: 'POST', path: '/chat', body: { text: 'hi' } }],
    ['backend_status', null],
  ]);
  assert.deepEqual(listened, ['phoenix:event', 'phoenix:events-status', 'phoenix:backend-status',
    'stop:phoenix:event', 'stop:phoenix:events-status', 'stop:phoenix:backend-status']);
  assert.equal(tauri.nativeShellAvailable(), true);
});

test('PHX-106/107 no credential, backend address or network primitive exists in webview code', () => {
  for (const path of ['main.tsx', 'adapters/core.ts', 'adapters/tauri.ts', 'adapters/ConnectedApp.tsx']) {
    const text = source(path);
    assert.doesNotMatch(text, /\btoken|bearer|authorization|phoenix\.auth|sec-websocket/i, path);
    assert.doesNotMatch(text, /\bfetch\(|XMLHttpRequest|new WebSocket|EventSource|https?:\/\/|ws:\/\/|localhost|127\.0\.0\.1|ollama|colibri/i, path);
    assert.doesNotMatch(text, /localStorage|sessionStorage|dangerouslySetInnerHTML|eval\(|new Function/, path);
  }
  // Only tauri.ts touches the IPC package, and only invoke/listen/isTauri from it.
  for (const path of ['main.tsx', 'adapters/core.ts', 'adapters/ConnectedApp.tsx', 'App.tsx']) assert.doesNotMatch(source(path), /@tauri-apps/, path);
  const imports = [...source('adapters/tauri.ts').matchAll(/import\s+(?:type\s+)?\{([^}]*)\}\s+from\s+'([^']+)'/g)].map((match) => [match[2], match[1].split(',').map((name) => name.trim()).sort()]);
  assert.deepEqual(imports, [['@tauri-apps/api/core', ['invoke', 'isTauri']], ['@tauri-apps/api/event', ['listen']], ['./core', ['ApiResult', 'HttpMethod', 'Transport', 'TransportHandlers']]]);
});

test('PHX-107 ConnectedApp renders the connection bar and the live App without inline styles', async () => {
  const connected = load('adapters/ConnectedApp.tsx');
  const { store } = await readyStore();
  const markup = renderToStaticMarkup(createElement(connected.ConnectedApp, { store }));
  assert.match(markup, /aria-label="Backend connection"/);
  assert.match(markup, /Connected to the local PHOENIX backend through the native shell\./);
  assert.match(markup, /D:\\docs \(active\)/);
  assert.match(markup, /Latest mission 11111111: waiting for your permission/);
  assert.match(markup, /notes\.txt lists three tasks\./);
  assert.doesNotMatch(markup, /style="/);
  const degraded = renderToStaticMarkup(createElement(connected.ConnectionBar, { connection: { ...store.getSnapshot().connection, backend: { state: 'degraded', code: 'python_not_found' } }, store }));
  assert.match(degraded, /Backend unavailable\. The backend Python environment was not found/);
  assert.doesNotMatch(degraded, /Document workspace/);
});

export { loader };
