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
  return { mission_id: MISSION, conversation_id: CONV, status: 'waiting_permission', path: 'complex', triage_outcome: 'decided', failure_code: null,
    responder_error: null, rerun_of: null, created_at: AT, started_at: AT, finished_at: null, request_text: 'Summarize notes.txt',
    answer_text: null, rerun_id: null, task_id: TASK, task_states: ['created', 'planning', 'running', 'waiting_permission'],
    permission_request: { request_id: REQ, mission_id: MISSION, status: 'pending', tool_id: 'filesystem.read_text', action: 'read_text',
      resource: 'D:\\docs\\notes.txt', risk_level: 'P2', requested_at: AT, decided_at: null }, ...overrides };
}
function settingsBody(overrides = {}) {
  return { workspace: { root: 'D:\\docs', status: 'active', updated_at: AT }, default_mission_path: 'auto',
    model_tiers: { phoenix: [{ provider_id: 'ollama', model_id: 'qwen3.5:4b' }, { provider_id: 'ollama', model_id: 'qwen3.5:2b' }] },
    default_tier: [{ provider_id: 'ollama', model_id: 'qwen3.5:4b' }], retention_days: 30, api: { host: '127.0.0.1', port: 5000 }, ...overrides };
}
function onboardingBody(overrides = {}) {
  return { complete: true, runtimes: [{ provider_id: 'ollama', display_name: 'Ollama', state: 'running' }],
    models: [
      { provider_id: 'ollama', model_id: 'qwen3.5:4b', required: true, registered: true, install_command: null },
      { provider_id: 'ollama', model_id: 'qwen3.5:2b', required: false, registered: true, install_command: null },
      { provider_id: 'ollama', model_id: 'qwen3.5:9b', required: true, registered: true, install_command: null },
    ],
    storage: { data_dir: 'D:\\phoenix-data', free_bytes: 5 * 1024 ** 3, database_ready: true },
    workspace: { root: 'D:\\docs', status: 'active', updated_at: AT }, ...overrides };
}
function capabilitiesBody(enabled = []) {
  const flags = [['workspace_documents', 'PHX-046', 'document_analyst'], ['memory', 'PHX-110', 'memory'], ['web_research', 'PHX-111', 'web_research'],
    ['computer_control', 'PHX-112', 'computer_control'], ['media', 'PHX-113', 'media'], ['document_generation', 'PHX-113', 'document_analyst'], ['voice', 'PHX-114', 'voice']];
  return { capabilities: flags.map(([id, ticket, agent]) => ({ capability_id: id, enabled: id === 'workspace_documents' || enabled.includes(id), ticket, agent_id: agent })) };
}
function backendRoutes(overrides = {}) {
  const routes = {
    'GET /settings': { status: 200, body: settingsBody() },
    'GET /onboarding': { status: 200, body: onboardingBody() },
    'GET /capabilities': { status: 200, body: capabilitiesBody() },
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
      { message_id: `${RERUN}:answer`, role: 'phoenix', text: 'notes.txt lists three tasks.', created_at: AT }]) },
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
    { id: MSG1, role: 'user', content: 'Summarize notes.txt' }, { id: `${RERUN}:answer`, role: 'assistant', content: '[Verified by LEVI]\nnotes.txt lists three tasks.' }] });

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
  assert.equal(view.connection.mission.path, 'complex');
  assert.equal(view.connection.mission.triageOutcome, 'decided');
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
  // Automatic is the default: no `path`, so PHOENIX triages the request (PHX-105).
  assert.equal(store.getSnapshot().connection.path, 'auto');
  assert.deepEqual(plain(transport.calls[0]), { method: 'POST', path: '/chat', body: { text: 'Hello PHOENIX' } });
  store.setPath('complex');
  await store.getSnapshot().chatPort.sendText('Read notes.txt');
  assert.deepEqual(plain(transport.calls.at(-1).body), { text: 'Read notes.txt', conversation_id: CONV, path: 'complex' });
  store.setPath('direct');
  await store.getSnapshot().chatPort.sendText('Hi');
  assert.deepEqual(plain(transport.calls.at(-1).body), { text: 'Hi', conversation_id: CONV, path: 'direct' });
  store.setPath('auto');
  await store.getSnapshot().chatPort.sendText('Again');
  assert.deepEqual(plain(transport.calls.at(-1).body), { text: 'Again', conversation_id: CONV });
  // A mission still being triaged has a null path and outcome.
  assert.deepEqual(plain(core.readMission(detail({ path: null, triage_outcome: null }))).path, null);
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
  const messages = core.toChatMessages(page([{ message_id: `${RERUN}:answer`, role: 'phoenix', text: long, created_at: AT }, { message_id: MSG2, role: 'system', text: 'x' }]), new Map([[RERUN, 'verified']]));
  // The 19-character badge "[Verified by LEVI]\n" leads the first part; nothing is clipped.
  assert.deepEqual(plain(messages.map((message) => [message.id, message.content.length])), [[`${RERUN}:answer:1`, 4000], [`${RERUN}:answer:2`, 29]]);
  assert.ok(messages[0].content.startsWith('[Verified by LEVI]\n'));
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
  for (const path of ['main.tsx', 'adapters/core.ts', 'adapters/tauri.ts', 'adapters/ConnectedApp.tsx', 'adapters/SetupViews.tsx']) {
    const text = source(path);
    assert.doesNotMatch(text, /\btoken|bearer|authorization|phoenix\.auth|sec-websocket/i, path);
    assert.doesNotMatch(text, /\bfetch\(|XMLHttpRequest|new WebSocket|EventSource|https?:\/\/|ws:\/\/|localhost|127\.0\.0\.1|ollama|colibri/i, path);
    assert.doesNotMatch(text, /localStorage|sessionStorage|dangerouslySetInnerHTML|eval\(|new Function/, path);
  }
  // Only tauri.ts touches the IPC package, and only invoke/listen/isTauri from it.
  for (const path of ['main.tsx', 'adapters/core.ts', 'adapters/ConnectedApp.tsx', 'adapters/SetupViews.tsx', 'App.tsx']) assert.doesNotMatch(source(path), /@tauri-apps/, path);
  const imports = [...source('adapters/tauri.ts').matchAll(/import\s+(?:type\s+)?\{([^}]*)\}\s+from\s+'([^']+)'/g)].map((match) => [match[2], match[1].split(',').map((name) => name.trim()).sort()]);
  assert.deepEqual(imports, [['@tauri-apps/api/core', ['invoke', 'isTauri']], ['@tauri-apps/api/event', ['listen']], ['./core', ['ApiResult', 'HttpMethod', 'Transport', 'TransportHandlers']]]);
});

test('PHX-107 ConnectedApp renders the connection bar and the live App without inline styles', async () => {
  const connected = load('adapters/ConnectedApp.tsx');
  const { store } = await readyStore();
  const markup = renderToStaticMarkup(createElement(connected.ConnectedApp, { store }));
  assert.match(markup, /aria-label="Backend connection"/);
  assert.match(markup, /Connected to the local PHOENIX backend through the native shell\./);
  assert.match(markup, /D:\\docs \(active\) · change it in Settings/);
  assert.match(markup, /Connected to the local PHOENIX backend — live data/);
  assert.match(markup, /Latest mission 11111111: waiting for your permission[^<]*· planned steps \(PHOENIX decided\)/);
  assert.match(markup, /<option value="auto" selected="">Automatic \(PHOENIX decides\)<\/option>/);
  assert.match(markup, /Override: direct answer/);
  assert.match(markup, /notes\.txt lists three tasks\./);
  assert.doesNotMatch(markup, /style="/);
  const degraded = renderToStaticMarkup(createElement(connected.ConnectionBar, { connection: { ...store.getSnapshot().connection, backend: { state: 'degraded', code: 'python_not_found' } }, store }));
  assert.match(degraded, /Backend unavailable\. The backend Python environment was not found/);
  assert.doesNotMatch(degraded, /Document workspace/);
});

// ---- consolidated review M1: an unverified answer never looks verified

function completed(overrides) {
  return detail({ status: 'completed', permission_request: null, ...overrides });
}

test('M1 verification labels: complex+completed is verified, direct and direct fallback are unverified', () => {
  const verified = core.readMission(completed({ path: 'complex', task_states: ['created', 'planning', 'running', 'verifying', 'completed'] }));
  assert.equal(verified.verification, 'verified');
  const direct = core.readMission(completed({ path: 'direct', task_id: null, task_states: [] }));
  assert.equal(direct.verification, 'unverified_direct');
  // PHX-109 fallback: the mission completed on the DIRECT path while its COMPLEX task failed.
  const fallback = core.readMission(completed({ path: 'complex', task_states: ['created', 'planning', 'failed'] }));
  assert.equal(fallback.verification, 'unverified_direct_fallback');
  assert.equal(core.readMission(completed({ path: null, task_states: [] })).verification, 'unverified_unknown');
  assert.equal(core.readMission(completed({ path: 'complex', task_states: [] })).verification, 'unverified_unknown');
  assert.equal(core.readMission(detail()).verification, undefined); // not completed: no label
  assert.equal(core.verificationOf({ status: 'failed', path: 'complex', taskStates: ['failed'] }), undefined);
});

test('M1 the top bar states verification in text and never calls a direct fallback planned steps', () => {
  const connected = load('adapters/ConnectedApp.tsx');
  const line = (overrides) => connected.missionLine(core.readMission(completed(overrides)));
  assert.equal(line({ path: 'complex', task_states: ['running', 'completed'] }),
    'Latest mission 11111111: completed · verified · planned steps (PHOENIX decided)');
  assert.equal(line({ path: 'complex', task_states: ['planning', 'failed'] }),
    'Latest mission 11111111: completed · unverified (direct fallback)');
  assert.doesNotMatch(line({ path: 'complex', task_states: ['planning', 'failed'] }), /planned steps/);
  assert.equal(line({ path: 'direct', triage_outcome: 'client', task_states: [] }),
    'Latest mission 11111111: completed · unverified · direct');
  assert.match(connected.missionLine(core.readMission(detail())), /waiting for your permission[^·]*· planned steps/);
});

test('M1 every PHOENIX answer in Chat carries a text badge from its own mission', async () => {
  const FALLBACK = '88888888-8888-4888-8888-888888888888';
  const DIRECT = '99999999-9999-4999-8999-999999999999';
  const LOST = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  const answers = [
    { message_id: MSG1, role: 'user', text: 'Q', created_at: AT },
    { message_id: `${RERUN}:answer`, role: 'phoenix', text: 'verified answer', created_at: AT },
    { message_id: `${FALLBACK}:answer`, role: 'phoenix', text: 'fallback answer', created_at: AT },
    { message_id: `${DIRECT}:answer`, role: 'phoenix', text: 'direct answer', created_at: AT },
    { message_id: `${LOST}:answer`, role: 'phoenix', text: 'unknown answer', created_at: AT },
  ];
  const { store, transport, scheduler } = await readyStore(backendRoutes({
    [`GET /conversations/${CONV}/messages?limit=1`]: { status: 200, body: page([], answers.length) },
    [`GET /conversations/${CONV}/messages?limit=64&offset=0`]: { status: 200, body: page(answers) },
    [`GET /missions/${FALLBACK}`]: { status: 200, body: completed({ mission_id: FALLBACK, path: 'complex', task_states: ['planning', 'failed'] }) },
    [`GET /missions/${DIRECT}`]: { status: 200, body: completed({ mission_id: DIRECT, path: 'direct', task_id: null, task_states: [] }) },
  }));
  const chat = app.boundedMessages(app.readChatPort(store.getSnapshot().chatPort).messages);
  assert.equal(chat.omitted, false);
  assert.deepEqual(plain(chat.messages.map((message) => message.content)), [
    'Q',
    '[Verified by LEVI]\nverified answer',
    '[Unverified · direct fallback]\nfallback answer',
    '[Unverified · direct answer]\ndirect answer',
    '[Unverified · verification status unavailable]\nunknown answer',
  ]);
  // Each answer's mission is read once; completed verification is cached across refreshes.
  const reads = () => transport.calls.filter((call) => call.path === `/missions/${FALLBACK}`).length;
  assert.equal(reads(), 1);
  transport.handlers.stream({ connected: true, code: null });
  await scheduler.flush();
  assert.ok(transport.calls.filter((call) => call.path.startsWith(`/conversations/${CONV}/messages`)).length > 2);
  assert.equal(reads(), 1);
  const markup = renderToStaticMarkup(createElement(load('adapters/ConnectedApp.tsx').ConnectedApp, { store }));
  assert.match(markup, /\[Unverified · direct fallback\]/);
  assert.match(markup, /\[Verified by LEVI\]/);
});

// PHX-115 (ADR-148): the backend's GET /metrics snapshot feeds the unchanged Observability reader.
function metricsBody(overrides = {}) {
  return {
    generated_at: '2026-10-21T10:00:00.123456+00:00', uptime_seconds: 42, ttft_basis: 'mission_first_answer',
    missions: { completed: 2, queued: 2, running: 2 },
    latency: [
      { stage: 'mission.first_answer', count: 2, p50_ms: 2500, p95_ms: 4000, max_ms: 4000, mean_ms: 3250 },
      { stage: 'agent.planner', count: 1, p50_ms: 900, p95_ms: 900, max_ms: 900, mean_ms: 900 },
      { stage: 'tool', count: 0, p50_ms: null, p95_ms: null, max_ms: null, mean_ms: null },
    ],
    generation: { missions_ingested: 2, generations: 5, input_tokens: 70, output_tokens: 40, duration: { stage: 'generation', count: 5 },
      output_tokens_per_second_mean: 21.5, models: [{ provider_id: 'ollama', model_id: 'qwen3.5:4b', generations: 5, input_tokens: 70, output_tokens: 40 }],
      provider_health: [{ provider_id: 'ollama', status: 'healthy' }] },
    tools: { calls: 4, succeeded: 3, failed: 1, success_rate: 0.75, by_status: { success: 3, denied: 1 } },
    permissions: { requested: 2, approved: 1, denied: 1, policy_denied: 0, by_risk: { P2: 2 } },
    verification: { passed: 2, failed: 1, by_verdict: { passed: 2, failed: 1 } },
    recoveries: { started: 1, completed: 1, exhausted: 0, by_action: { retry_same: 1 }, by_outcome: { recovered: 1 } },
    resources: { process_rss_bytes: 200 * 1024 * 1024, system_ram_total_bytes: 24 * 1024 ** 3, system_ram_available_bytes: 10 * 1024 ** 3, process_cpu_percent: 3.5,
      vram: { status: 'ok', total_vram_bytes: 4000 * 1024 * 1024, models: [{ model_id: 'qwen3.5:4b', size_bytes: 5000 * 1024 * 1024, vram_bytes: 4000 * 1024 * 1024 }] } },
    events: { observed: 30, rejected: 0, stream_dropped: 0, stream_rejected: 0 },
    logs: [
      { id: 'log-1', ts: '2026-10-21T09:59:59.5+00:00', level: 'info', subsystem: 'phoenix.events', message: 'task.state_changed scope=mission mission_status=queued', correlation_id: MISSION, mission_id: MISSION, event_type: 'task.state_changed' },
      { id: 'log-2', ts: '2026-10-21T10:00:00+00:00', level: 'warning', subsystem: 'phoenix.events', message: 'tool.failed step_id=s1 code=tool_rejected', correlation_id: MISSION, task_id: TASK, step_id: 's1' },
      { id: 'log-3', ts: 'not a time', level: 'info', subsystem: 'x', message: 'dropped' },
      { id: 'log-4', ts: '2026-10-21T10:00:00Z', level: 'trace', subsystem: 'x', message: 'dropped' },
    ],
    ...overrides,
  };
}

test('PHX-115 GET /metrics becomes admitted metric and log entries; event metrics fill the rest', async () => {
  const { store, transport, scheduler } = await readyStore(backendRoutes({ 'GET /metrics': { status: 200, body: metricsBody() } }));
  assert.ok(transport.calls.some((call) => call.method === 'GET' && call.path === '/metrics'));
  let diagnostics = app.readObservabilityPort(store.getSnapshot().observabilityPort);
  assert.equal(diagnostics.rejected, false);
  assert.equal(diagnostics.status, 'connected');
  const byId = new Map(diagnostics.metrics.map((metric) => [metric.id, plain(metric)]));
  assert.deepEqual(byId.get('backend:latency:mission.first_answer:p50'), { id: 'backend:latency:mission.first_answer:p50', name: 'ttft', value: 2500, unit: 'ms p50 mission.first_answer', recordedAt: '2026-10-21T10:00:00.123456Z' });
  assert.equal(byId.get('backend:latency:agent.planner:p95').name, 'task_step_latency');
  assert.ok(!byId.has('backend:latency:tool:p50')); // no observation, no number
  assert.deepEqual(plain(byId.get('backend:throughput')), { id: 'backend:throughput', name: 'tokens_per_sec', value: 21.5, unit: 'tok/s mean', recordedAt: '2026-10-21T10:00:00.123456Z' });
  assert.equal(byId.get('backend:generations').value, '5 generations · 70 in / 40 out tok');
  assert.equal(byId.get('backend:health:ollama').value, 'ollama healthy');
  assert.equal(byId.get('backend:ram:process').value, 200);
  assert.equal(byId.get('backend:vram:total').value, 4000);
  assert.equal(byId.get('backend:vram:qwen3.5:4b').value, 'qwen3.5:4b: 4000 of 5000 MiB on GPU');
  assert.equal(byId.get('backend:cpu:process').value, 3.5);
  assert.equal(byId.get('backend:tools:rate').value, 75);
  assert.equal(byId.get('backend:permissions').value, 'requested 2 · approved 1 · denied 1 · policy denied 0');
  assert.match(byId.get('backend:verification').value, /^LEVI passed 2 · failed 1/);
  assert.match(byId.get('backend:recoveries').value, /^started 1 · completed 1 · exhausted 0/);
  assert.deepEqual(plain(diagnostics.logs.map((log) => [log.id, log.severity, log.correlationId ?? null, log.stepId ?? null])), [
    ['backend:log-1', 'info', MISSION, null], ['backend:log-2', 'warning', MISSION, 's1']]);
  assert.equal(diagnostics.logs[1].taskId, TASK);

  // A terminal mission event refreshes the snapshot; live event metrics are kept within App's 64.
  transport.calls.length = 0;
  for (let index = 0; index < 70; index += 1) transport.handlers.event(envelope('step.completed', { step_id: 's1', duration_ms: index }));
  transport.handlers.event(envelope('task.state_changed', { scope: 'mission', mission_status: 'completed' }));
  await scheduler.flush();
  assert.ok(transport.calls.some((call) => call.path === '/metrics'));
  diagnostics = app.readObservabilityPort(store.getSnapshot().observabilityPort);
  assert.equal(diagnostics.rejected, false);
  assert.equal(diagnostics.metrics.length, 64);
  assert.ok(diagnostics.metrics.some((metric) => metric.id.startsWith('backend:')));
  assert.equal(diagnostics.metrics.at(-1).value, 69);

  // VRAM unavailable is said, not hidden; malformed snapshots are ignored and keep the last good one.
  const unavailable = core.toBackendDiagnostics(metricsBody({ resources: { vram: { status: 'unavailable' } } }));
  assert.equal(unavailable.metrics.find((metric) => metric.id === 'backend:vram:total').value, 'unavailable');
  assert.ok(!unavailable.metrics.some((metric) => metric.name === 'ram'));
  assert.equal(core.toBackendDiagnostics({ ...metricsBody(), generated_at: 'yesterday' }), undefined);
  assert.equal(core.toBackendDiagnostics([]), undefined);
  const hostile = core.toBackendDiagnostics(metricsBody({ latency: Array.from({ length: 40 }, (_, index) => ({ stage: `s${index}`, p50_ms: 1, p95_ms: 2 })), logs: Array.from({ length: 90 }, (_, index) => ({ id: `log-${index}`, ts: AT, level: 'info', subsystem: 'phoenix.events', message: 'm' })) }));
  assert.ok(hostile.metrics.length <= core.BACKEND_METRICS);
  assert.equal(hostile.logs.length, 64);
  assert.equal(app.readObservabilityPort({ status: 'connected', metrics: hostile.metrics, logs: hostile.logs, traces: [] }).rejected, false);
});

test('PHX-115 a backend without /metrics leaves the event metrics as they were', async () => {
  const { store, transport } = await readyStore();
  assert.ok(transport.calls.some((call) => call.path === '/metrics'));
  const port = store.getSnapshot().observabilityPort;
  assert.deepEqual(plain(port.metrics), []);
  assert.deepEqual(plain(port.logs), []);
});

// ---- PHX-108 (ADR-110 to ADR-114): onboarding, settings and capability views

const setup = load('adapters/SetupViews.tsx');
const connectedModule = load('adapters/ConnectedApp.tsx');
function incomplete() {
  return onboardingBody({ complete: false, runtimes: [{ provider_id: 'ollama', display_name: 'Ollama', state: 'not_running' }],
    models: [
      { provider_id: 'ollama', model_id: 'qwen3.5:4b', required: true, registered: false, install_command: 'ollama pull qwen3.5:4b' },
      { provider_id: 'ollama', model_id: 'qwen3.5:9b', required: true, registered: false, install_command: 'ollama pull qwen3.5:9b' },
    ], workspace: { root: null, status: 'unset', updated_at: null } });
}
function render(component, props) { return renderToStaticMarkup(createElement(component, props)); }

test('PHX-108 setup readers admit the documented shapes and refuse malformed ones', () => {
  const onboarding = core.readOnboarding(onboardingBody());
  assert.deepEqual(plain(onboarding), {
    complete: true, runtimes: [{ providerId: 'ollama', displayName: 'Ollama', state: 'running' }],
    models: [
      { providerId: 'ollama', modelId: 'qwen3.5:4b', required: true, registered: true, installCommand: null },
      { providerId: 'ollama', modelId: 'qwen3.5:2b', required: false, registered: true, installCommand: null },
      { providerId: 'ollama', modelId: 'qwen3.5:9b', required: true, registered: true, installCommand: null },
    ],
    storage: { dataDir: 'D:\\phoenix-data', freeBytes: 5 * 1024 ** 3, databaseReady: true },
    workspace: { root: 'D:\\docs', status: 'active' },
  });
  for (const bad of [null, [], {}, onboardingBody({ complete: 'yes' }), onboardingBody({ runtimes: [{ provider_id: 'ollama', display_name: 'Ollama', state: 'started' }] }),
    onboardingBody({ models: [{ provider_id: 'ollama', model_id: 'x', required: 1, registered: true, install_command: null }] }),
    onboardingBody({ storage: { data_dir: 'D:\\d', free_bytes: 1, database_ready: 'true' } }), onboardingBody({ workspace: null })]) {
    assert.equal(core.readOnboarding(bad), undefined);
  }
  assert.equal(core.readOnboarding(onboardingBody({ storage: { data_dir: 'D:\\d', free_bytes: -1, database_ready: true } })).storage.freeBytes, null);
  const flags = core.readCapabilities({ capabilities: [...capabilitiesBody(['web_research']).capabilities, { capability_id: 'x', enabled: 'no' }, 7] });
  assert.equal(flags.length, 7);
  assert.equal(flags.find((flag) => flag.capabilityId === 'web_research').enabled, true);
  assert.equal(core.readCapabilities({}), undefined);
  assert.deepEqual(plain(core.readSettings(settingsBody({ default_mission_path: 'direct' }))), {
    defaultMissionPath: 'direct', modelTiers: [{ role: 'phoenix', models: ['qwen3.5:4b', 'qwen3.5:2b'] }], defaultTier: ['qwen3.5:4b'], retentionDays: 30,
  });
  assert.equal(core.readSettings(settingsBody({ default_mission_path: 'fast' })), undefined);
});

test('PHX-108 the store loads setup state on ready and the saved default becomes the mission path', async () => {
  const { store, transport, scheduler } = await readyStore(backendRoutes({ 'GET /settings': { status: 200, body: settingsBody({ default_mission_path: 'direct' }) } }));
  const paths = transport.calls.map((call) => `${call.method} ${call.path}`);
  for (const route of ['GET /settings', 'GET /onboarding', 'GET /capabilities']) assert.ok(paths.includes(route), route);
  const connection = store.getSnapshot().connection;
  assert.equal(connection.path, 'direct');
  assert.equal(connection.onboarding.complete, true);
  assert.equal(connection.capabilities.length, 7);
  await store.sendText('hello');
  assert.deepEqual(plain(transport.calls.at(-1)), { method: 'POST', path: '/chat', body: { text: 'hello', conversation_id: CONV, path: 'direct' } });
  // A later settings read does not override a choice made in the bar for this launch.
  store.setPath('auto');
  store.schedule('settings');
  await scheduler.flush();
  assert.equal(store.getSnapshot().connection.path, 'auto');
});

test('PHX-108 saving the default mission path patches exactly that field and explains refusals', async () => {
  const saved = settingsBody({ default_mission_path: 'complex' });
  const { store, transport } = await readyStore(backendRoutes({ 'PATCH /settings': { status: 200, body: saved } }));
  await store.setDefaultPath('complex');
  assert.deepEqual(plain(transport.calls.at(-1)), { method: 'PATCH', path: '/settings', body: { default_mission_path: 'complex' } });
  const connection = store.getSnapshot().connection;
  assert.equal(connection.path, 'complex');
  assert.equal(connection.settings.defaultMissionPath, 'complex');
  assert.match(connection.settingsMessage, /Saved\. New missions use: Planned steps/);
  const calls = transport.calls.length;
  await store.setDefaultPath('fast');
  assert.equal(transport.calls.length, calls); // never sent
  const refused = await readyStore(backendRoutes({ 'PATCH /settings': { status: 422, body: { error: { code: 'invalid_request', message: 'x', request_id: 'r' } } } }));
  await refused.store.setDefaultPath('direct');
  assert.match(refused.store.getSnapshot().connection.settingsMessage, /not saved \(invalid_request\)/);
  assert.equal(refused.store.getSnapshot().connection.path, 'auto');
});

test('PHX-108 first run shows the ordered setup steps instead of App until the user continues', async () => {
  const { store } = await readyStore(backendRoutes({ 'GET /onboarding': { status: 200, body: incomplete() } }));
  assert.equal(store.firstRunVisible(), true);
  const markup = render(connectedModule.ConnectedApp, { store });
  assert.match(markup, /aria-label="First-run setup"/);
  assert.match(markup, /Welcome — set up PHOENIX/);
  const order = ['Start the local model runtime', 'Install the required models', 'Check data storage', 'Choose your document folder'].map((title) => markup.indexOf(title));
  assert.ok(order.every((index, position) => index > 0 && (position === 0 || index > order[position - 1])), String(order));
  assert.match(markup, /Ollama is installed but not running\. Start Ollama, then select Re-check\./);
  assert.match(markup, /qwen3\.5:4b \(required\) — checked once the model runtime is running/);
  assert.match(markup, /D:\\phoenix-data \(5\.0 GB free\)/);
  assert.match(markup, /Continue without finishing/);
  assert.match(markup, />Re-check</);
  assert.doesNotMatch(markup, /Primary navigation|style="/);
  store.dismissOnboarding();
  assert.equal(store.firstRunVisible(), false);
  const after = render(connectedModule.ConnectedApp, { store });
  assert.match(after, /Primary navigation/);
  assert.doesNotMatch(after, /First-run setup/);
});

test('PHX-108 a running runtime with missing models shows the install command from the backend', async () => {
  const body = incomplete();
  body.runtimes[0].state = 'running';
  const { store } = await readyStore(backendRoutes({ 'GET /onboarding': { status: 200, body } }));
  const markup = render(setup.OnboardingPanel, { connection: store.getSnapshot().connection, store, firstRun: true });
  assert.match(markup, /Ollama is running\./);
  assert.match(markup, /qwen3\.5:9b \(required\) — not installed; install it with <code>ollama pull qwen3\.5:9b<\/code>/);
});

test('PHX-108 re-check reloads onboarding, models and settings; the first-run screen polls', async () => {
  const intervals = [];
  const transport = mockTransport(backendRoutes({ 'GET /onboarding': { status: 200, body: incomplete() } }));
  const scheduler = {
    setTimeout(callback) { setImmediate(callback); return 1; }, clearTimeout() {},
    setInterval(callback) { intervals.push(callback); return 1; }, clearInterval() {},
  };
  const store = new core.PhoenixStore(transport, scheduler);
  await store.start();
  for (let tick = 0; tick < 60; tick += 1) await new Promise((resolve) => setImmediate(resolve));
  const before = transport.calls.length;
  await store.recheck();
  assert.deepEqual(transport.calls.slice(before).map((call) => call.path).sort(), ['/models', '/onboarding', '/settings']);
  assert.equal(store.getSnapshot().connection.checking, false);
  const polled = transport.calls.length;
  intervals[0]();
  for (let tick = 0; tick < 60; tick += 1) await new Promise((resolve) => setImmediate(resolve));
  const onboardingCalls = (from) => transport.calls.slice(from).filter((call) => call.path === '/onboarding').length;
  assert.equal(onboardingCalls(polled), 1);
  store.dismissOnboarding();
  const quiet = transport.calls.length;
  intervals[0]();
  for (let tick = 0; tick < 60; tick += 1) await new Promise((resolve) => setImmediate(resolve));
  assert.equal(onboardingCalls(quiet), 0); // no onboarding polling once dismissed
});

test('PHX-108 completed setup skips the first-run screen; Onboarding stays reachable as a view', async () => {
  const { store } = await readyStore();
  assert.equal(store.firstRunVisible(), false);
  const markup = render(connectedModule.ConnectedApp, { store });
  assert.match(markup, /Primary navigation/);
  const panel = render(setup.OnboardingPanel, { connection: store.getSnapshot().connection, store });
  assert.match(panel, /Setup is complete\. Every check passed\./);
  assert.equal((panel.match(/— done/g) ?? []).length, 4);
  assert.doesNotMatch(panel, /Continue/);
});

test('PHX-108 Settings is writable: workspace folder, default mission path, read-only tiers and honest placeholders', async () => {
  const { store } = await readyStore();
  const markup = render(setup.SettingsPanel, { connection: store.getSnapshot().connection, store });
  for (const heading of ['Document workspace', 'Default mission path', 'Models', 'Voice', 'Memory and retention', 'Privacy and permissions']) assert.ok(markup.includes(`>${heading}</h4>`), heading);
  assert.match(markup, /<input id="settings-workspace-root" type="text"/);
  assert.match(markup, /<select id="default-mission-path">/);
  assert.match(markup, /<option value="auto" selected="">Automatic \(PHOENIX decides\)<\/option>/);
  assert.match(markup, />Save default</);
  assert.match(markup, /phoenix: qwen3\.5:4b → qwen3\.5:2b/);
  assert.match(markup, /Voice is not yet enabled in this build \(arrives with PHX-114\)\. Text chat stays fully available\./);
  assert.match(markup, /Long-term memory is not yet enabled in this build \(arrives with PHX-110\)\. Mission history is kept for 30 days\./);
  assert.match(markup, /PermissionEngine alone authorizes actions/);
  assert.doesNotMatch(markup, /style="/);
  const down = render(setup.SettingsPanel, { connection: { ...store.getSnapshot().connection, backend: { state: 'degraded', code: 'backend_exited' } }, store });
  assert.match(down, /Settings are unavailable until the local backend is connected\./);
  assert.doesNotMatch(down, /<input|<select/);
});

test('PHX-108 Computer Control, Research and Media follow the backend capability flags', async () => {
  const { store } = await readyStore();
  const connection = store.getSnapshot().connection;
  const computer = render(setup.CapabilityPanel, { view: 'computer', connection });
  assert.match(computer, /Computer control: not yet enabled<\/strong> — arrives with PHX-112\. PHOENIX does not attempt it in missions until then\./);
  assert.match(computer, /Carried by KAKASHI \(Computer control\)\./);
  assert.match(render(setup.CapabilityPanel, { view: 'research', connection }), /Web research: not yet enabled<\/strong> — arrives with PHX-111/);
  const media = render(setup.CapabilityPanel, { view: 'media', connection });
  assert.match(media, /Spotify and YouTube: not yet enabled<\/strong> — arrives with PHX-113/);
  assert.match(media, /Document generation: not yet enabled/);
  // A later ticket only flips the backend flag; the view follows without a frontend change.
  const flipped = await readyStore(backendRoutes({ 'GET /capabilities': { status: 200, body: capabilitiesBody(['web_research']) } }));
  const research = render(setup.CapabilityPanel, { view: 'research', connection: flipped.store.getSnapshot().connection });
  assert.match(research, /Web research: enabled<\/strong> — ask PHOENIX in Home \/ Chat/);
  const unknown = render(setup.CapabilityPanel, { view: 'computer', connection: { ...connection, capabilities: undefined } });
  assert.match(unknown, /Capability status is unavailable until the local backend is connected\./);
  for (const markup of [computer, media, research, unknown]) assert.doesNotMatch(markup, /<(button|input|form)\b|style="/);
});

test('PHX-108 App renders caller-supplied panels only for the five views it has no port for', () => {
  const supplied = createElement('p', null, 'SUPPLIED-PANEL');
  const panels = { onboarding: supplied, settings: supplied, computer: supplied, research: supplied, media: supplied };
  for (const id of ['onboarding', 'settings', 'computer', 'research', 'media']) {
    assert.match(renderToStaticMarkup(createElement(app.ViewPanel, { view: app.resolveView(id), panels })), /SUPPLIED-PANEL/, id);
  }
  for (const id of ['home', 'agents', 'tasks', 'permissions', 'memory', 'files', 'models', 'voice', 'observability']) {
    assert.doesNotMatch(renderToStaticMarkup(createElement(app.ViewPanel, { view: app.resolveView(id), panels: { ...panels, [id]: supplied } })), /SUPPLIED-PANEL/, id);
  }
  // Without panels the honest defaults remain.
  assert.match(renderToStaticMarkup(createElement(app.ViewPanel, { view: app.resolveView('computer') })), /This workspace is not connected yet\./);
  assert.match(renderToStaticMarkup(createElement(app.default)), /Interface shell — backend integration pending/);
  assert.match(renderToStaticMarkup(createElement(app.default, { statusLine: 'LIVE' })), /<p class="integration-status">LIVE<\/p>/);
});

test('PHX-108 the connection bar no longer edits the workspace; Settings and Onboarding do', async () => {
  const { store } = await readyStore();
  const bar = render(connectedModule.ConnectionBar, { connection: store.getSnapshot().connection, store });
  assert.doesNotMatch(bar, /<input|Set folder/);
  assert.match(bar, /Mission path \(this launch\)/);
  await store.setWorkspace('D:\\link');
  const panel = render(setup.OnboardingPanel, { connection: store.getSnapshot().connection, store });
  assert.match(panel, /Links, junctions and reparse points are not accepted/);
});

export { loader };
