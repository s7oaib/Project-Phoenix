import { useEffect, useRef, useState, type FormEvent } from 'react';

type NavigationGroup = 'Mission' | 'Workspace' | 'System';
type NavigationItem = { id: string; label: string; group: NavigationGroup; purpose: string };

export const views = [
  { id: 'onboarding', label: 'Onboarding', group: 'Mission', purpose: 'A guided place to set up PHOENIX and review your local preferences.' },
  { id: 'home', label: 'Home / Chat', group: 'Mission', purpose: 'Your starting point for conversation and the work ahead.' },
  { id: 'agents', label: 'Agent Network', group: 'Mission', purpose: 'Understand the specialist roles that support PHOENIX and their work.' },
  { id: 'tasks', label: 'Task Graph', group: 'Mission', purpose: 'Follow task plans, dependencies and verification as work progresses.' },
  { id: 'memory', label: 'Memory', group: 'Workspace', purpose: 'Review the context you choose to retain and manage its scope.' },
  { id: 'files', label: 'Files / Documents', group: 'Workspace', purpose: 'Organize source material and review document artifacts in one workspace.' },
  { id: 'computer', label: 'Computer Control', group: 'Workspace', purpose: 'Review computer interactions and their required permissions.' },
  { id: 'research', label: 'Research', group: 'Workspace', purpose: 'Explore research findings with their sources and provenance.' },
  { id: 'media', label: 'Media', group: 'Workspace', purpose: 'A workspace for your authorized Spotify and YouTube connections.' },
  { id: 'permissions', label: 'Permissions', group: 'System', purpose: 'Review requested actions, their scope and the choices available to you.' },
  { id: 'models', label: 'Models', group: 'System', purpose: 'Review local model configuration and capability information.' },
  { id: 'voice', label: 'Voice', group: 'System', purpose: 'Manage voice preferences while keeping a text-first path available.' },
  { id: 'settings', label: 'Settings', group: 'System', purpose: 'A home for application preferences and local configuration.' },
  { id: 'observability', label: 'Observability / Developer', group: 'System', purpose: 'Inspect execution evidence and diagnostics when their integration is available.' },
] as const satisfies readonly NavigationItem[];

export type ViewId = (typeof views)[number]['id'];
export type ViewDefinition = (typeof views)[number];
const groups: readonly NavigationGroup[] = ['Mission', 'Workspace', 'System'];

export function resolveView(value: unknown): ViewDefinition {
  return views.find((view) => view.id === value) ?? views[1];
}

export type ChatMessage = { id: string; role: 'user' | 'assistant'; content: string };
export type ChatPort = {
  status: 'connected' | 'unavailable' | 'degraded';
  messages: readonly ChatMessage[];
  sendText(text: string): Promise<void>;
};
export const DRAFT_LIMIT = 4000;
export const MESSAGE_LIMIT = 64;
export const MESSAGE_ID_LIMIT = 128;
export type SendFlight = { pending: boolean; cancelled: boolean };
export type SendOutcome = 'acknowledged' | 'failed' | 'cancelled' | 'busy' | 'invalid' | 'unavailable';

// Read data fields only: malformed accessors must not execute during presentation.
function dataValue(value: unknown, key: string): unknown {
  if (typeof value !== 'object' || value === null) return undefined;
  try {
    const field = Object.getOwnPropertyDescriptor(value, key);
    return field && 'value' in field ? field.value : undefined;
  } catch {
    return undefined;
  }
}

export function readChatPort(value: unknown): ChatPort | undefined {
  const status = dataValue(value, 'status');
  const messages = dataValue(value, 'messages');
  const sendText = dataValue(value, 'sendText');
  try {
    if ((status !== 'connected' && status !== 'unavailable' && status !== 'degraded') ||
        !Array.isArray(messages) || typeof sendText !== 'function') return undefined;
    return { status, messages, sendText: sendText as ChatPort['sendText'] };
  } catch {
    return undefined;
  }
}

export function validateDraft(value: unknown): string | undefined {
  if (typeof value !== 'string' || value.length > DRAFT_LIMIT) return undefined;
  const text = value.trim();
  return text.length > 0 && text.length <= DRAFT_LIMIT ? text : undefined;
}

export function boundedMessages(value: unknown): { messages: ChatMessage[]; omitted: boolean } {
  const messages: ChatMessage[] = [];
  try {
    if (!Array.isArray(value)) return { messages, omitted: true };
    let omitted = value.length > MESSAGE_LIMIT;
    for (let index = 0; index < Math.min(value.length, MESSAGE_LIMIT); index += 1) {
      const entry = dataValue(value, `${index}`);
      const id = dataValue(entry, 'id');
      const role = dataValue(entry, 'role');
      const content = dataValue(entry, 'content');
      if (typeof entry !== 'object' || entry === null || Reflect.ownKeys(entry).length !== 3 ||
          typeof id !== 'string' || id.trim().length === 0 || id.length > MESSAGE_ID_LIMIT ||
          (role !== 'user' && role !== 'assistant') || typeof content !== 'string' ||
          content.trim().length === 0 || content.length > DRAFT_LIMIT ||
          messages.some((message) => message.id === id)) {
        omitted = true;
        continue;
      }
      messages.push({ id, role, content });
    }
    return { messages, omitted };
  } catch {
    return { messages: [], omitted: true };
  }
}

export async function sendDraft(value: unknown, draft: unknown, flight: SendFlight): Promise<SendOutcome> {
  if (flight.pending) return 'busy';
  const port = readChatPort(value);
  if (!port || port.status !== 'connected') return 'unavailable';
  const text = validateDraft(draft);
  if (text === undefined) return 'invalid';
  flight.pending = true;
  flight.cancelled = false;
  try {
    const acknowledgement = port.sendText(text);
    // An accidental synchronous return or arbitrary thenable is not an acknowledgement.
    if (!(acknowledgement instanceof Promise)) return 'failed';
    const result = await acknowledgement;
    if (flight.cancelled) return 'cancelled';
    return result === undefined ? 'acknowledged' : 'failed';
  } catch {
    return flight.cancelled ? 'cancelled' : 'failed';
  } finally {
    flight.pending = false;
  }
}

export function cancelWait(flight: SendFlight): void {
  if (flight.pending) flight.cancelled = true;
}

export function acknowledgedDraft(current: string, submitted: string, outcome: SendOutcome): string {
  return outcome === 'acknowledged' && current === submitted ? '' : current;
}

function useChatDraft(chatPort: ChatPort | undefined) {
  const [draft, setDraft] = useState('');
  const [pending, setPending] = useState(false);
  const [feedback, setFeedback] = useState('');
  const flight = useRef<SendFlight>({ pending: false, cancelled: false });

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (flight.current.pending) return;
    const submitted = draft;
    const request = sendDraft(chatPort, submitted, flight.current);
    if (flight.current.pending) {
      setPending(true);
      setFeedback('Waiting for adapter acknowledgement. The draft is still local.');
    }
    const outcome = await request;
    setDraft((current) => acknowledgedDraft(current, submitted, outcome));
    setPending(false);
    setFeedback(outcome === 'acknowledged' ? 'Adapter acknowledged submission. Replies appear only in its supplied snapshot.' :
      outcome === 'cancelled' ? 'Waiting cancelled. Delivery outcome is unknown; your draft is retained.' :
      outcome === 'failed' ? 'Submission was not acknowledged. Your draft is retained; check the connection before retrying.' :
      outcome === 'invalid' ? 'Enter 1–4000 characters of non-whitespace text.' :
      'Sending is unavailable. Your draft remains local.');
  }

  function cancel() {
    cancelWait(flight.current);
    setFeedback('Waiting cancelled locally. This does not cancel delivery; further sends stay blocked until the adapter settles.');
  }

  return { draft, setDraft, pending, feedback, submit, cancel };
}

type ChatDraft = ReturnType<typeof useChatDraft>;

export function ChatWorkspace({ chatPort, chat }: { chatPort?: ChatPort; chat: ChatDraft }) {
  const port = readChatPort(chatPort);
  const connected = port?.status === 'connected';
  const snapshot = boundedMessages(port?.messages ?? []);
  const valid = validateDraft(chat.draft) !== undefined;
  const reason = chat.pending ? 'A submission is pending; duplicate sends are blocked.' :
    !connected ? 'Backend connection unavailable. Sending requires a separately supplied connected adapter.' :
    !valid ? 'Enter 1–4000 characters of non-whitespace text to send.' :
    'Send submits trimmed text to the supplied adapter; server permissions still apply.';

  return (
    <div className="conversation-layout">
      <section className="chat-panel" aria-labelledby="conversation-heading">
        <header className="chat-heading">
          <p className="eyebrow">Text first</p>
          <h3 id="conversation-heading">Conversation</h3>
          <p>{connected ? 'Caller-supplied adapter. This interface does not establish live backend connectivity.' :
            port?.status === 'degraded' ? 'Adapter degraded. Sending is unavailable; your draft remains local.' :
            'Not connected. Backend chat integration is unavailable.'}</p>
        </header>
        <div className="transcript" role="region" aria-label="Conversation transcript" tabIndex={0}>
          {snapshot.messages.length === 0 ? (
            <p className="empty-conversation">No messages supplied. Draft locally; a conversation appears only from a connected integration.</p>
          ) : (
            <ol className="message-list">
              {snapshot.messages.map((message) => (
                <li className={`message message-${message.role}`} key={message.id}>
                  <h4>{message.role === 'user' ? 'User' : 'Assistant'}</h4>
                  <p className="message-content">{message.content}</p>
                </li>
              ))}
            </ol>
          )}
        </div>
        {snapshot.omitted && <p className="chat-notice">Some entries are omitted because they are malformed or exceed display limits. No message is partially clipped.</p>}
        <form className="composer" onSubmit={chat.submit} aria-busy={chat.pending}>
          <label htmlFor="chat-draft">Local draft only</label>
          <p id="draft-help">Kept in this app session only. Nothing is stored or sent automatically.</p>
          <textarea id="chat-draft" name="draft" rows={4} maxLength={DRAFT_LIMIT}
            value={chat.draft} readOnly={chat.pending}
            aria-describedby="draft-help draft-count send-reason"
            onChange={(event) => chat.setDraft(event.currentTarget.value)} />
          <p id="draft-count">{chat.draft.length} / {DRAFT_LIMIT} characters{chat.draft.length >= DRAFT_LIMIT ? ' — character limit reached' : ''}</p>
          <p id="send-reason">{reason}</p>
          <div className="composer-actions">
            <button className="send-button" type="submit" disabled={!connected || !valid || chat.pending} aria-describedby="send-reason">{chat.pending ? 'Sending…' : 'Send'}</button>
            {chat.pending && <button type="button" className="cancel-button" onClick={chat.cancel}>Cancel waiting</button>}
          </div>
          <p className="send-feedback" role="status" aria-live="polite" aria-atomic="true">{chat.feedback}</p>
        </form>
      </section>
      <aside className="voice-panel" aria-labelledby="voice-heading">
        <p className="eyebrow">Voice</p>
        <h3 id="voice-heading">Voice unavailable</h3>
        <p>Not connected</p>
        <button type="button" disabled aria-describedby="voice-reason">Listen with microphone</button>
        <p id="voice-reason">Microphone control is unavailable. Audio capture, wake detection, STT and TTS integration are deferred.</p>
        <p>Text entry remains available without enabling a microphone.</p>
      </aside>
    </div>
  );
}

// Frozen presentation reference; Python's canonical registry remains authoritative.
const canonicalAgents = [
  { internalId: 'phoenix', alias: 'PHOENIX', professionalRole: 'Supreme orchestrator' },
  { internalId: 'strategist', alias: 'AIZEN', professionalRole: 'Chief strategist/master planner' },
  { internalId: 'intelligence', alias: 'ARMIN', professionalRole: 'Intelligence/reporting' },
  { internalId: 'automation', alias: 'LAW', professionalRole: 'Automation/workflows' },
  { internalId: 'mission_coordinator', alias: 'ERWIN', professionalRole: 'Mission/task coordination' },
  { internalId: 'project_manager', alias: 'NAMI', professionalRole: 'Project management' },
  { internalId: 'build_engineer', alias: 'FRANKY', professionalRole: 'Build/engineering' },
  { internalId: 'document_analyst', alias: 'ROBIN', professionalRole: 'Documents/knowledge extraction' },
  { internalId: 'system_intelligence', alias: 'LELOUCH', professionalRole: 'Active system intelligence/monitoring' },
  { internalId: 'diagnostics', alias: 'CHOPPER', professionalRole: 'Diagnostics' },
  { internalId: 'perimeter_security', alias: 'GOJO', professionalRole: 'Security/perimeter' },
  { internalId: 'health_biometrics', alias: 'MIGHT GUY', professionalRole: 'Health/biometrics' },
  { internalId: 'ethics_compliance', alias: 'FUJITORA', professionalRole: 'Legal/ethics/compliance' },
  { internalId: 'media', alias: 'BROOK', professionalRole: 'Spotify/YouTube/media' },
  { internalId: 'failsafe', alias: 'KAIDO', professionalRole: 'Emergency degraded fail-safe' },
  { internalId: 'infrastructure', alias: 'WHITEBEARD', professionalRole: 'Infrastructure/stability' },
  { internalId: 'knowledge_rag', alias: 'VEGAPUNK', professionalRole: 'Knowledge indexing/RAG' },
  { internalId: 'background_optimizer', alias: 'SHIKAMARU', professionalRole: 'Background optimization' },
  { internalId: 'scenario_analysis', alias: 'LIGHT', professionalRole: 'Scenario/trade-off analysis' },
  { internalId: 'heavy_compute', alias: 'GARP', professionalRole: 'Heavy compute/batch work' },
  { internalId: 'threat_recon', alias: 'SASUKE', professionalRole: 'Threat reconnaissance' },
  { internalId: 'priority_executor', alias: 'THORFINN', professionalRole: 'High-priority execution' },
  { internalId: 'user_interaction', alias: 'NARUTO', professionalRole: 'User interaction support' },
  { internalId: 'silent_monitor', alias: 'ITACHI', professionalRole: 'Logs/anomaly monitoring' },
  { internalId: 'adaptive_recovery', alias: 'MAHORAGA', professionalRole: 'Adaptive recovery/self-healing' },
  { internalId: 'memory', alias: 'PUDDING', professionalRole: 'Long-term memory' },
  { internalId: 'web_research', alias: 'URAHARA', professionalRole: 'Real-time web research' },
  { internalId: 'voice', alias: 'UTA', professionalRole: 'Voice/wake interface' },
  { internalId: 'computer_control', alias: 'KAKASHI', professionalRole: 'Computer control' },
  { internalId: 'verifier', alias: 'LEVI', professionalRole: 'Verification/QA' },
] as const;
export const agentRoster = Object.freeze(canonicalAgents.map((agent) => Object.freeze(agent)));
export type AgentId = (typeof canonicalAgents)[number]['internalId'];
export const AGENT_QUERY_LIMIT = 80;
export type AgentState = 'idle' | 'working' | 'waiting_permission' | 'degraded';
export type AgentStatus = {
  readonly internalId: AgentId;
  readonly state: AgentState;
  readonly currentTask?: string;
  readonly provider?: string;
};
export type AgentPort = {
  readonly status: 'connected' | 'unavailable' | 'degraded';
  readonly agents: readonly AgentStatus[];
};

export function findAgent(value: unknown) {
  return typeof value === 'string' ? agentRoster.find((agent) => agent.internalId === value) : undefined;
}

export function boundedAgentQuery(value: unknown): string {
  return typeof value === 'string' ? value.slice(0, AGENT_QUERY_LIMIT) : '';
}

export function filterAgents(value: unknown) {
  const query = boundedAgentQuery(value).toLowerCase();
  return agentRoster.filter((agent) => agent.alias.toLowerCase().includes(query) ||
    agent.internalId.toLowerCase().includes(query) || agent.professionalRole.toLowerCase().includes(query));
}

// No property reads/coercion on caller objects. Reflective proxy traps are caught;
// caller-owned same-realm objects are trusted infrastructure, not a JS sandbox.
function agentDataFields(value: unknown, required: readonly string[], optional: readonly string[] = []) {
  if (typeof value !== 'object' || value === null) return undefined;
  const keys = Reflect.ownKeys(value);
  if (keys.length < required.length || keys.length > required.length + optional.length) return undefined;
  const fields: Record<string, unknown> = {};
  for (const key of keys) {
    if (typeof key !== 'string' || (!required.includes(key) && !optional.includes(key))) return undefined;
    const field = Object.getOwnPropertyDescriptor(value, key);
    if (!field || !('value' in field)) return undefined;
    fields[key] = field.value;
  }
  return required.every((key) => keys.includes(key)) ? fields : undefined;
}

export function readAgentSnapshot(value: unknown): { status: AgentPort['status']; agents: AgentStatus[]; rejected: boolean } {
  const unavailable = { status: 'unavailable' as const, agents: [], rejected: value !== undefined };
  try {
    const port = agentDataFields(value, ['status', 'agents']);
    if (!port || (port.status !== 'connected' && port.status !== 'unavailable' && port.status !== 'degraded')) return unavailable;
    const entries: unknown = port.agents;
    if (!Array.isArray(entries)) return unavailable;
    const length = dataValue(entries, 'length');
    if (typeof length !== 'number' || !Number.isInteger(length) || length < 0 || length > agentRoster.length ||
        Reflect.ownKeys(entries).length !== length + 1) return unavailable;
    const agents: AgentStatus[] = [];
    for (let index = 0; index < length; index += 1) {
      const entry = agentDataFields(dataValue(entries, `${index}`), ['internalId', 'state'], ['currentTask', 'provider']);
      const identity = findAgent(entry?.internalId);
      const state = entry?.state;
      if (!entry || !identity || (state !== 'idle' && state !== 'working' && state !== 'waiting_permission' && state !== 'degraded') ||
          agents.some((agent) => agent.internalId === identity.internalId) ||
          ('currentTask' in entry && (typeof entry.currentTask !== 'string' || entry.currentTask.length > 240)) ||
          ('provider' in entry && (typeof entry.provider !== 'string' || entry.provider.length > 120))) return unavailable;
      agents.push({ internalId: identity.internalId, state,
        ...(typeof entry.currentTask === 'string' ? { currentTask: entry.currentTask } : {}),
        ...(typeof entry.provider === 'string' ? { provider: entry.provider } : {}) });
    }
    // Disconnected/degraded input cannot advertise live activity, even if records exist.
    return { status: port.status, agents: port.status === 'connected' ? agents : [], rejected: false };
  } catch {
    return unavailable;
  }
}

function useAgentDirectory() {
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState<AgentId>('phoenix');
  function search(value: unknown) { setQuery(boundedAgentQuery(value)); }
  function select(value: unknown) {
    const agent = findAgent(value);
    if (agent) setSelectedId(agent.internalId);
  }
  return { query, selectedId, search, select };
}
type AgentDirectory = ReturnType<typeof useAgentDirectory>;

export function AgentNetwork({ agentPort, directory }: { agentPort?: AgentPort; directory: AgentDirectory }) {
  const matches = filterAgents(directory.query);
  const selected = findAgent(directory.selectedId);
  const snapshot = readAgentSnapshot(agentPort);
  const evidence = snapshot.agents.find((agent) => agent.internalId === selected?.internalId);
  const selectedInMatches = matches.some((agent) => agent.internalId === selected?.internalId);
  return (
    <section className="agent-network" aria-labelledby="agent-network-heading">
      <header className="agent-network-header">
        <p className="eyebrow">Defined roles</p>
        <h3 id="agent-network-heading">Agent Network</h3>
        <p>30 defined logical roles: PHOENIX and 29 specialists. Definitions do not mean agents are initialized or running.</p>
        <p className="agent-availability">{snapshot.status === 'connected' ? 'Caller-supplied status evidence; live transport is not implemented.' :
          snapshot.status === 'degraded' ? 'Live agent state not connected. Supplied status is degraded.' : 'Live agent state not connected.'}</p>
        {snapshot.rejected && <p className="agent-notice">Supplied status snapshot was rejected because it is malformed or exceeds display limits.</p>}
      </header>
      <div className="agent-search">
        <label htmlFor="agent-query">Search defined agents</label>
        <p id="agent-search-help">Alias, internal ID or professional role. Local search, up to 80 characters.</p>
        <div className="agent-search-controls">
          <input id="agent-query" type="search" maxLength={AGENT_QUERY_LIMIT} value={directory.query}
            aria-describedby="agent-search-help agent-match-count" onChange={(event) => directory.search(event.currentTarget.value)} />
          <button type="button" onClick={() => directory.search('')}>Clear search</button>
        </div>
        <p id="agent-match-count" role="status" aria-live="polite" aria-atomic="true">{matches.length} of 30 defined roles match</p>
      </div>
      <div className="agent-directory-layout">
        <section className="agent-results" aria-label="Matching defined agents">
          {matches.length === 0 ? <p className="agent-empty">No defined agents match. Clear search to restore all 30 roles.</p> :
            <ul className="agent-list">
              {matches.map((agent) => (
                <li key={agent.internalId}>
                  <button className="agent-card" type="button" aria-pressed={selected?.internalId === agent.internalId}
                    aria-controls="agent-detail" onClick={() => directory.select(agent.internalId)}>
                    <span className="agent-alias">{agent.alias}</span>
                    <span className="agent-role">{agent.professionalRole}</span>
                    <span className="agent-id">{agent.internalId}</span>
                  </button>
                </li>
              ))}
            </ul>}
        </section>
        <section className="agent-detail" id="agent-detail" aria-labelledby="agent-detail-heading">
          <h4 id="agent-detail-heading">Selected agent details</h4>
          {selected ? <>
            <p className="agent-selection" role="status" aria-live="polite" aria-atomic="true">Selected: {selected.alias}{!selectedInMatches ? ' — outside the current search results. Clear search to show its selection button.' : ''}</p>
            <dl>
              <dt>Display alias</dt><dd>{selected.alias}</dd>
              <dt>Canonical internal ID</dt><dd>{selected.internalId}</dd>
              <dt>Professional role</dt><dd>{selected.professionalRole}</dd>
            </dl>
            <p>{selected.internalId === 'phoenix' ? 'PHOENIX is the sole supreme orchestrator.' : 'Specialist role subordinate to PHOENIX.'}</p>
            <h5>Runtime evidence</h5>
            <p>{evidence ? 'Supplied presentation state: ' : 'Status not reported'}{evidence?.state}</p>
            {evidence?.currentTask !== undefined && <p className="agent-evidence">Supplied current task: {evidence.currentTask}</p>}
            {evidence?.provider !== undefined && <p className="agent-evidence">Supplied model/provider: {evidence.provider}</p>}
          </> : <p>No canonical agent selected. Choose a defined role from the directory.</p>}
          <p className="agent-boundary">Aliases and status text confer no permission. This directory cannot launch, command or authorize agents.</p>
        </section>
      </div>
    </section>
  );
}

export const taskStates = Object.freeze(['created', 'planning', 'ready', 'running', 'waiting_permission', 'verifying', 'completed', 'failed', 'recovering', 'degraded', 'cancelled'] as const);
export type TaskState = (typeof taskStates)[number];
export type TaskStep = {
  readonly stepId: string;
  readonly ownerAgentId: AgentId;
  readonly objective: string;
  readonly dependsOn: readonly string[];
  readonly inputRefs: readonly string[];
  readonly expectedOutput: string;
  readonly timeoutSeconds: number;
  readonly completionRule: string;
};
export type TaskGraphSnapshot = { readonly taskId: string; readonly taskState?: TaskState; readonly steps: readonly TaskStep[] };
export type TaskGraphPort = { readonly status: 'connected' | 'unavailable' | 'degraded'; readonly graph?: TaskGraphSnapshot };

function taskText(value: unknown, limit: number): value is string {
  return typeof value === 'string' && value.length <= limit && value.trim().length > 0;
}

function taskArray(value: unknown, limit: number): unknown[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const length = dataValue(value, 'length');
  if (typeof length !== 'number' || !Number.isInteger(length) || length < 0 || length > limit ||
      Reflect.ownKeys(value).length !== length + 1) return undefined;
  const result: unknown[] = [];
  for (let index = 0; index < length; index += 1) {
    const field = Object.getOwnPropertyDescriptor(value, `${index}`);
    if (!field || !('value' in field)) return undefined;
    result.push(field.value);
  }
  return result;
}

// Kahn traversal: rescan original source positions after every newly eligible node.
// At forty nodes this bounded scan preserves Python's heap tie-break without a library.
export function taskTopology(steps: readonly TaskStep[]) {
  if (steps.length === 0) return undefined;
  const incoming = steps.map((step) => step.dependsOn.length);
  const emitted = steps.map(() => false);
  const ordered: TaskStep[] = [];
  const layers: number[] = [];
  const roots = steps.filter((step) => step.dependsOn.length === 0).map((step) => step.stepId);
  let edgeCount = 0;
  for (const step of steps) edgeCount += step.dependsOn.length;
  while (ordered.length < steps.length) {
    const position = steps.findIndex((_, index) => !emitted[index] && incoming[index] === 0);
    const step = steps[position];
    if (!step) return undefined;
    const layer = step.dependsOn.length === 0 ? 0 : 1 + Math.max(...step.dependsOn.map((id) => layers[ordered.findIndex((item) => item.stepId === id)] ?? 0));
    ordered.push(step);
    layers.push(layer);
    emitted[position] = true;
    steps.forEach((item, index) => {
      if (item.dependsOn.includes(step.stepId)) incoming[index] = (incoming[index] ?? 0) - 1;
    });
  }
  return { ordered: Object.freeze(ordered), layers: Object.freeze(layers), roots: Object.freeze(roots), edgeCount };
}

export function readTaskGraphPort(value: unknown) {
  const unavailable = { status: 'unavailable' as TaskGraphPort['status'], graph: undefined, rejected: value !== undefined };
  try {
    const port = agentDataFields(value, ['status'], ['graph']);
    if (!port || (port.status !== 'connected' && port.status !== 'unavailable' && port.status !== 'degraded')) return unavailable;
    if (port.status !== 'connected' || port.graph === undefined) return { status: port.status, graph: undefined, rejected: false };
    const graph = agentDataFields(port.graph, ['taskId', 'steps'], ['taskState']);
    const entries = taskArray(graph?.steps, 40);
    const state = graph?.taskState;
    if (!graph || !taskText(graph.taskId, 128) || !entries || entries.length === 0 ||
        ('taskState' in graph && (typeof state !== 'string' || !taskStates.some((item) => item === state)))) return unavailable;
    const steps: TaskStep[] = [];
    let edges = 0;
    for (const entry of entries) {
      const fields = agentDataFields(entry, ['stepId', 'ownerAgentId', 'objective', 'dependsOn', 'inputRefs', 'expectedOutput', 'timeoutSeconds', 'completionRule']);
      const owner = findAgent(fields?.ownerAgentId);
      const dependencies = taskArray(fields?.dependsOn, 12);
      const references = taskArray(fields?.inputRefs, 8);
      if (!fields || !taskText(fields.stepId, 128) || !owner || !taskText(fields.objective, 600) ||
          !taskText(fields.expectedOutput, 400) || !taskText(fields.completionRule, 400) ||
          typeof fields.timeoutSeconds !== 'number' || !Number.isFinite(fields.timeoutSeconds) || fields.timeoutSeconds <= 0 || fields.timeoutSeconds > 86400 ||
          !dependencies || !references || steps.some((step) => step.stepId === fields.stepId)) return unavailable;
      const dependsOn: string[] = [];
      const inputRefs: string[] = [];
      for (const id of dependencies) {
        if (!taskText(id, 128) || id === fields.stepId || dependsOn.includes(id)) return unavailable;
        dependsOn.push(id);
      }
      for (const reference of references) {
        if (!taskText(reference, 128)) return unavailable;
        inputRefs.push(reference);
      }
      edges += dependsOn.length;
      if (edges > 120) return unavailable;
      steps.push(Object.freeze({ stepId: fields.stepId, ownerAgentId: owner.internalId, objective: fields.objective,
        dependsOn: Object.freeze(dependsOn), inputRefs: Object.freeze(inputRefs), expectedOutput: fields.expectedOutput,
        timeoutSeconds: fields.timeoutSeconds, completionRule: fields.completionRule }));
    }
    if (steps.some((step) => step.dependsOn.some((id) => !steps.some((item) => item.stepId === id)))) return unavailable;
    const topology = taskTopology(steps);
    if (!topology) return unavailable;
    const taskState = taskStates.find((item) => item === state);
    const admitted = Object.freeze({ taskId: graph.taskId, ...(taskState ? { taskState } : {}),
      steps: Object.freeze(steps), ...topology });
    return { status: port.status, graph: admitted, rejected: false };
  } catch {
    return unavailable;
  }
}

function useTaskGraph(taskPort: TaskGraphPort | undefined) {
  const snapshot = readTaskGraphPort(taskPort);
  const graph = snapshot.graph;
  const [selection, setTaskSelection] = useState<{ taskId: string; stepId: string } | undefined>(undefined);
  const selected = (selection?.taskId === graph?.taskId ? graph?.ordered.find((step) => step.stepId === selection?.stepId) : undefined) ?? graph?.ordered[0];
  // Adjust only changed identity fields, so replaced/disconnected graphs cannot leak
  // a stale selection. This guarded render adjustment is local React state only.
  if (selection?.taskId !== graph?.taskId || selection?.stepId !== selected?.stepId) {
    setTaskSelection(graph && selected ? { taskId: graph.taskId, stepId: selected.stepId } : undefined);
  }
  function select(value: unknown) {
    const step = graph?.ordered.find((item) => item.stepId === value);
    if (graph && step) setTaskSelection({ taskId: graph.taskId, stepId: step.stepId });
  }
  return { snapshot, selected, select };
}
type TaskPresentation = ReturnType<typeof useTaskGraph>;

export function TaskWorkspace({ task }: { task: TaskPresentation }) {
  const { snapshot, selected } = task;
  const graph = snapshot.graph;
  const owner = findAgent(selected?.ownerAgentId);
  return (
    <section className="task-workspace" aria-labelledby="task-graph-heading">
      <header className="task-header">
        <p className="eyebrow">Read-only definition</p>
        <h3 id="task-graph-heading">Task Graph</h3>
        <p>Inspect supplied dependencies and step definitions. Structure does not establish runtime readiness or execution.</p>
        <p>{snapshot.status === 'connected' ? 'Caller-supplied port; live task transport is not implemented.' :
          snapshot.status === 'degraded' ? 'Task connection degraded. Supplied graph is hidden.' : 'No task graph connected.'}</p>
        {snapshot.rejected && <p className="task-notice">Task graph snapshot rejected. Data is malformed or exceeds display limits.</p>}
      </header>
      {!graph ? <p className="task-empty">No task graph supplied for display. No tasks or progress are generated by this interface.</p> : <>
        <section className="task-summary" aria-label="Supplied task evidence">
          <p>Task ID: <span className="task-text">{graph.taskId}</span></p>
          <p>Caller-supplied task state; not live transport or verified completion: {graph.taskState ?? 'Not reported'}</p>
          <p>{graph.steps.length} defined steps · {graph.edgeCount} dependency edges</p>
          <p>Structural root steps: {graph.roots.join(', ')}</p>
          <p>A completed marker does not prove execution or LEVI verification. A waiting_permission marker is not an approval request.</p>
        </section>
        <div className="task-layout">
          <section className="task-steps" aria-label="Steps in source-priority topological order">
            <ol className="task-list">
              {graph.ordered.map((step, index) => (
                <li key={step.stepId}>
                  <button type="button" className="task-step-button" aria-pressed={selected?.stepId === step.stepId}
                    aria-controls="task-detail" onClick={() => task.select(step.stepId)}>
                    <span className="task-step-id">{step.stepId}</span>
                    <span>Dependency layer {graph.layers[index]} · {step.dependsOn.length === 0 ? 'Root step' : 'Downstream step'}</span>
                    <span>Depends on: {step.dependsOn.length ? step.dependsOn.join(', ') : 'None'}</span>
                    <span>Owner ID: {step.ownerAgentId}</span>
                  </button>
                </li>
              ))}
            </ol>
          </section>
          <section className="task-detail" id="task-detail" aria-labelledby="task-detail-heading">
            <h4 id="task-detail-heading">Selected step definition</h4>
            {selected && <>
              <p role="status" aria-live="polite" aria-atomic="true">Selected step: {selected.stepId}</p>
              <dl>
                <dt>Canonical owner ID</dt><dd>{selected.ownerAgentId}</dd>
                <dt>Display alias</dt><dd>{owner?.alias}</dd>
                <dt>Objective</dt><dd className="task-text">{selected.objective}</dd>
                <dt>Expected output</dt><dd className="task-text">{selected.expectedOutput}</dd>
                <dt>Completion rule</dt><dd className="task-text">{selected.completionRule}</dd>
                <dt>Timeout definition</dt><dd>{selected.timeoutSeconds} seconds (no timer is running)</dd>
                <dt>Dependencies</dt><dd>{selected.dependsOn.length ? selected.dependsOn.join(', ') : 'None — structural root'}</dd>
                <dt>Input references (inert text)</dt><dd>{selected.inputRefs.length ? <ul>{selected.inputRefs.map((reference, index) => <li className="task-text" key={index}>{reference}</li>)}</ul> : 'None supplied'}</dd>
              </dl>
            </>}
            <p>Definitions and state markers confer no permission or verified success. Step selection only changes this local detail view.</p>
          </section>
        </div>
      </>}
    </section>
  );
}

export const permissionRisks = Object.freeze(['P0', 'P1', 'P2', 'P3', 'P4', 'P5'] as const);
export const permissionStatuses = Object.freeze(['allowed', 'approval_required', 'denied', 'blocked'] as const);
export const permissionEvidenceRules = Object.freeze([
  { reasonCode: 'p0_internal', status: 'allowed', risks: ['P0'], evidence: undefined },
  { reasonCode: 'explicit_user_approval', status: 'allowed', risks: ['P1', 'P2', 'P3', 'P4'], evidence: 'userDecisionId' },
  { reasonCode: 'explicit_user_denial', status: 'denied', risks: ['P0', 'P1', 'P2', 'P3', 'P4'], evidence: 'userDecisionId' },
  { reasonCode: 'matching_grant', status: 'allowed', risks: ['P1', 'P2', 'P3'], evidence: 'matchedGrantId' },
  { reasonCode: 'matching_policy', status: 'allowed', risks: ['P2', 'P3'], evidence: 'matchedPolicyRuleId' },
  { reasonCode: 'p1_scope_required', status: 'approval_required', risks: ['P1'], evidence: undefined },
  { reasonCode: 'p2_approval_required', status: 'approval_required', risks: ['P2'], evidence: undefined },
  { reasonCode: 'p3_approval_required', status: 'approval_required', risks: ['P3'], evidence: undefined },
  { reasonCode: 'p4_explicit_approval_required', status: 'approval_required', risks: ['P4'], evidence: undefined },
  { reasonCode: 'p5_blocked', status: 'blocked', risks: ['P5'], evidence: undefined },
] as const);
export type PermissionRequest = {
  readonly requestId: string; readonly actorId: string; readonly toolId: string;
  readonly action: string; readonly resource: string; readonly reason: string; readonly requestedAt: string;
  readonly taskId?: string; readonly sessionId?: string;
};
export type PermissionEvaluation = {
  readonly requestId: string; readonly actorId: string; readonly toolId: string;
  readonly riskLevel: (typeof permissionRisks)[number]; readonly permissionRequirement: string;
  readonly status: (typeof permissionStatuses)[number]; readonly reasonCode: (typeof permissionEvidenceRules)[number]['reasonCode'];
  readonly matchedGrantId?: string; readonly matchedPolicyRuleId?: string; readonly userDecisionId?: string;
};
export type PermissionEntry = { readonly request: PermissionRequest; readonly evaluation?: PermissionEvaluation };
export type DecisionIntent = 'approved' | 'denied';
export type PermissionPort = {
  readonly status: 'connected' | 'unavailable' | 'degraded'; readonly requests: readonly PermissionEntry[];
  readonly submitDecision?: (requestId: string, decision: DecisionIntent) => Promise<void>;
};
export type PermissionFlight = { pending: boolean; submittedIds: string[] };
export type IntentOutcome = 'submitted' | 'failed' | 'busy' | 'waiting_evidence' | 'ineligible';

function permissionId(value: unknown): value is string {
  return taskText(value, 128) && value === value.trim();
}

export function permissionUtc(value: unknown): value is string {
  if (typeof value !== 'string' || value.length > 64) return false;
  const parts = value.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|\+00:00)$/);
  if (!parts) return false;
  const year = Number(parts[1]);
  const month = Number(parts[2]);
  const day = Number(parts[3]);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1] ?? 0;
  return year > 0 && month >= 1 && month <= 12 && day >= 1 && day <= days &&
    Number(parts[4]) <= 23 && Number(parts[5]) <= 59 && Number(parts[6]) <= 59;
}

function readPermissionRequest(value: unknown): PermissionRequest | undefined {
  const fields = agentDataFields(value, ['requestId', 'actorId', 'toolId', 'action', 'resource', 'reason', 'requestedAt'], ['taskId', 'sessionId']);
  if (!fields || !permissionId(fields.requestId) || !permissionId(fields.actorId) || !permissionId(fields.toolId) ||
      !taskText(fields.action, 256) || !taskText(fields.resource, 512) || !taskText(fields.reason, 2000) || !permissionUtc(fields.requestedAt)) return undefined;
  for (const key of ['taskId', 'sessionId']) if (key in fields && !permissionId(fields[key])) return undefined;
  // Shape/primitive validation above precedes this narrow copy-only type assertion.
  return Object.freeze({ ...fields }) as PermissionRequest;
}

function readPermissionEvaluation(value: unknown, request: PermissionRequest): PermissionEvaluation | undefined {
  const fields = agentDataFields(value, ['requestId', 'actorId', 'toolId', 'riskLevel', 'permissionRequirement', 'status', 'reasonCode'], ['matchedGrantId', 'matchedPolicyRuleId', 'userDecisionId']);
  if (!fields || fields.requestId !== request.requestId || fields.actorId !== request.actorId || fields.toolId !== request.toolId ||
      !taskText(fields.permissionRequirement, 256)) return undefined;
  // Mirror only PHX-041's output-shape consistency. No policy or grant evaluation.
  const rule = permissionEvidenceRules.find((item) => item.reasonCode === fields.reasonCode);
  if (!rule || fields.status !== rule.status || !rule.risks.some((risk) => risk === fields.riskLevel)) return undefined;
  for (const key of ['matchedGrantId', 'matchedPolicyRuleId', 'userDecisionId']) {
    if ((key in fields) !== (key === rule.evidence) || (key in fields && !permissionId(fields[key]))) return undefined;
  }
  return Object.freeze({ ...fields }) as PermissionEvaluation;
}

export function readPermissionPort(value: unknown) {
  const unavailable = { status: 'unavailable' as PermissionPort['status'], requests: [] as readonly PermissionEntry[], submitDecision: undefined as PermissionPort['submitDecision'], rejected: value !== undefined };
  try {
    const fields = agentDataFields(value, ['status', 'requests'], ['submitDecision']);
    if (!fields || (fields.status !== 'connected' && fields.status !== 'unavailable' && fields.status !== 'degraded') ||
        ('submitDecision' in fields && typeof fields.submitDecision !== 'function')) return unavailable;
    const entries = taskArray(fields.requests, 64);
    if (!entries) return unavailable;
    const requests: PermissionEntry[] = [];
    for (const entry of entries) {
      const record = agentDataFields(entry, ['request'], ['evaluation']);
      const request = readPermissionRequest(record?.request);
      if (!record || !request || requests.some((item) => item.request.requestId === request.requestId)) return unavailable;
      const evaluation = 'evaluation' in record ? readPermissionEvaluation(record.evaluation, request) : undefined;
      if ('evaluation' in record && !evaluation) return unavailable;
      requests.push(Object.freeze({ request, ...(evaluation ? { evaluation } : {}) }));
    }
    if (fields.status !== 'connected') return { ...unavailable, status: fields.status, rejected: false };
    return { status: fields.status, requests: Object.freeze(requests),
      submitDecision: fields.submitDecision as PermissionPort['submitDecision'], rejected: false };
  } catch {
    return unavailable;
  }
}

export function permissionNeedsIntent(entry: PermissionEntry | undefined): boolean {
  const evaluation = entry?.evaluation;
  return evaluation?.status === 'approval_required' && evaluation.riskLevel !== 'P0' && evaluation.riskLevel !== 'P5';
}

export function prunePermissionIntents(snapshot: ReturnType<typeof readPermissionPort>, flight: PermissionFlight) {
  if (snapshot.status !== 'connected' || snapshot.rejected) return;
  flight.submittedIds = flight.submittedIds.filter((id) => {
    const entry = snapshot.requests.find((item) => item.request.requestId === id);
    return entry !== undefined && (!entry.evaluation || entry.evaluation.status === 'approval_required');
  });
}

export async function deliverPermissionIntent(value: unknown, requestId: unknown, decision: unknown, flight: PermissionFlight): Promise<IntentOutcome> {
  if (flight.pending) return 'busy';
  const port = readPermissionPort(value);
  prunePermissionIntents(port, flight);
  const entry = port.requests.find((item) => item.request.requestId === requestId);
  if (port.status !== 'connected' || !port.submitDecision || !entry || !permissionNeedsIntent(entry) ||
      (decision !== 'approved' && decision !== 'denied')) return 'ineligible';
  if (flight.submittedIds.includes(entry.request.requestId)) return 'waiting_evidence';
  flight.pending = true;
  try {
    const acknowledgement = port.submitDecision(entry.request.requestId, decision);
    if (!(acknowledgement instanceof Promise) || await acknowledgement !== undefined) return 'failed';
    flight.submittedIds.push(entry.request.requestId);
    return 'submitted';
  } catch {
    return 'failed';
  } finally {
    flight.pending = false;
  }
}

export function filterPermissionRequests(entries: readonly PermissionEntry[], value: unknown) {
  const query = boundedAgentQuery(value).toLowerCase();
  return entries.filter(({ request, evaluation }) => {
    const text = [request.requestId, request.actorId, request.toolId, request.action, request.resource, request.reason,
      request.requestedAt, request.taskId, request.sessionId, evaluation?.riskLevel, evaluation?.status, evaluation?.reasonCode,
      evaluation?.permissionRequirement, evaluation?.matchedGrantId, evaluation?.matchedPolicyRuleId, evaluation?.userDecisionId];
    return text.some((field) => typeof field === 'string' && field.toLowerCase().includes(query));
  });
}

function usePermissionCenter(permissionPort: PermissionPort | undefined) {
  const snapshot = readPermissionPort(permissionPort);
  const [query, setPermissionQuery] = useState('');
  const [selectedId, setSelectedPermissionId] = useState<string | undefined>(undefined);
  const [pending, setPermissionPending] = useState(false);
  const [feedback, setPermissionFeedback] = useState('');
  const [feedbackId, setPermissionFeedbackId] = useState<string | undefined>(undefined);
  const flight = useRef<PermissionFlight>({ pending: false, submittedIds: [] });
  prunePermissionIntents(snapshot, flight.current);
  const selected = snapshot.requests.find((entry) => entry.request.requestId === selectedId) ?? snapshot.requests[0];
  if (selectedId !== selected?.request.requestId) setSelectedPermissionId(selected?.request.requestId);
  function search(value: unknown) { setPermissionQuery(boundedAgentQuery(value)); }
  function select(value: unknown) {
    const entry = snapshot.requests.find((item) => item.request.requestId === value);
    if (entry) setSelectedPermissionId(entry.request.requestId);
  }
  async function submit(decision: DecisionIntent) {
    if (flight.current.pending) return;
    const requestId = selected?.request.requestId;
    const delivery = deliverPermissionIntent(permissionPort, requestId, decision, flight.current);
    setPermissionFeedbackId(requestId);
    if (flight.current.pending) {
      setPermissionPending(true);
      setPermissionFeedback('Waiting for adapter acknowledgement. Duplicate intents are blocked; delivery cannot be cancelled here.');
    }
    const outcome = await delivery;
    setPermissionPending(false);
    setPermissionFeedback(outcome === 'submitted' ? 'Intent submitted to the adapter; waiting for updated permission evidence. This is not authorization or execution.' :
      outcome === 'failed' ? 'Intent delivery was not acknowledged. Request evidence is unchanged; you may deliberately retry.' :
      outcome === 'waiting_evidence' ? 'Intent already submitted; waiting for updated permission evidence.' : 'Decision submission is unavailable for this request.');
  }
  return { snapshot, query, selected, search, select, submit, pending,
    submitted: selected !== undefined && flight.current.submittedIds.includes(selected.request.requestId),
    feedback: feedbackId === selected?.request.requestId ? feedback : '' };
}
type PermissionPresentation = ReturnType<typeof usePermissionCenter>;

export function PermissionCenter({ permissions }: { permissions: PermissionPresentation }) {
  const { snapshot, selected } = permissions;
  const matches = filterPermissionRequests(snapshot.requests, permissions.query);
  const request = selected?.request;
  const evaluation = selected?.evaluation;
  const reviewable = permissionNeedsIntent(selected);
  const reason = permissions.pending ? 'One intent is pending. Duplicate decisions are blocked until the adapter settles.' :
    permissions.submitted ? 'Intent submitted; waiting for updated permission evidence.' :
    !snapshot.submitDecision ? 'Decision submission is not connected. A trusted adapter is required.' :
    'These buttons deliver only this request-specific human intent. PermissionEngine remains authoritative.';
  return (
    <section className="permission-center" aria-labelledby="permission-heading">
      <header className="permission-header">
        <p className="eyebrow">Human intent · supplied evidence</p>
        <h3 id="permission-heading">Permission Center</h3>
        <p>Review requests. PermissionEngine is the sole authorization authority; this interface creates no grants and executes no tools.</p>
        <p>{snapshot.status === 'connected' ? 'Caller-supplied evidence. Live permission transport is not implemented.' :
          snapshot.status === 'degraded' ? 'Permission source degraded. Supplied requests are hidden.' : 'No permission source connected.'}</p>
        {snapshot.rejected && <p>Permission snapshot rejected because it is malformed or exceeds display limits.</p>}
      </header>
      <div className="permission-search">
        <label htmlFor="permission-query">Search permission evidence</label>
        <p id="permission-search-help">Local text search only, up to 80 characters.</p>
        <div className="permission-search-controls">
          <input id="permission-query" type="search" maxLength={80} value={permissions.query} aria-describedby="permission-search-help permission-match-count"
            onChange={(event) => permissions.search(event.currentTarget.value)} />
          <button type="button" onClick={() => permissions.search('')}>Clear search</button>
        </div>
        <p id="permission-match-count" role="status" aria-live="polite" aria-atomic="true">{matches.length} of {snapshot.requests.length} supplied requests match</p>
      </div>
      <div className="permission-layout">
        <section className="permission-results" aria-label="Matching permission requests">
          {snapshot.requests.length === 0 ? <p>No permission requests supplied.</p> : matches.length === 0 ? <p>No requests match. Clear search to restore supplied requests.</p> :
            <ul className="permission-list">{matches.map((entry) => <li key={entry.request.requestId}>
              <button className="permission-card" type="button" aria-pressed={request?.requestId === entry.request.requestId} aria-controls="permission-detail"
                onClick={() => permissions.select(entry.request.requestId)}>
                <span className="permission-id">{entry.request.requestId}</span><span>{entry.request.action}</span>
                <span>{entry.evaluation ? `Supplied evaluation: ${entry.evaluation.riskLevel} · ${entry.evaluation.status}` : 'Evaluation not reported'}</span>
              </button>
            </li>)}</ul>}
        </section>
        <section className="permission-detail" id="permission-detail" aria-labelledby="permission-detail-heading">
          <h4 id="permission-detail-heading">Selected request evidence</h4>
          {!request ? <p>No admitted request selected.</p> : <>
            <p role="status" aria-live="polite" aria-atomic="true">Selected request: {request.requestId}{!matches.some((entry) => entry.request.requestId === request.requestId) ? ' — outside the current filter. Clear search to show its selection button.' : ''}</p>
            <dl>
              <dt>Request ID</dt><dd>{request.requestId}</dd><dt>Actor / agent ID</dt><dd>{request.actorId}</dd>
              <dt>Tool ID</dt><dd>{request.toolId}</dd><dt>Requested action</dt><dd className="permission-text">{request.action}</dd>
              <dt>Resource / scope</dt><dd className="permission-text">{request.resource}</dd><dt>Reason (untrusted text)</dt><dd className="permission-text">{request.reason}</dd>
              <dt>Requested at (supplied UTC)</dt><dd>{request.requestedAt}</dd>
              {request.taskId !== undefined && <><dt>Task scope ID</dt><dd>{request.taskId}</dd></>}
              {request.sessionId !== undefined && <><dt>Session scope ID</dt><dd>{request.sessionId}</dd></>}
            </dl>
            <h5>Caller-supplied evaluation</h5>
            {!evaluation ? <p>Evaluation not reported. Risk, status and decision actions are not inferred.</p> : <>
              <dl><dt>Risk level</dt><dd>{evaluation.riskLevel}</dd><dt>Permission requirement</dt><dd className="permission-text">{evaluation.permissionRequirement}</dd>
                <dt>Supplied status</dt><dd>{evaluation.status}</dd><dt>Reason code</dt><dd>{evaluation.reasonCode}</dd>
                {evaluation.matchedGrantId !== undefined && <><dt>Matched grant evidence ID</dt><dd>{evaluation.matchedGrantId}</dd></>}
                {evaluation.matchedPolicyRuleId !== undefined && <><dt>Matched policy evidence ID</dt><dd>{evaluation.matchedPolicyRuleId}</dd></>}
                {evaluation.userDecisionId !== undefined && <><dt>User decision evidence ID</dt><dd>{evaluation.userDecisionId}</dd></>}
              </dl>
              <p>Supplied evaluation is not proof of live transport, audit persistence, tool execution or success.</p>
              {evaluation.riskLevel === 'P5' && <p>P5 is unconditionally blocked. No decision intent is available.</p>}
            </>}
            {reviewable && <div className="permission-intent">
              <p id="permission-intent-reason">{reason}</p>
              <div className="permission-actions" aria-busy={permissions.pending}>
                <button type="button" disabled={!snapshot.submitDecision || permissions.pending || permissions.submitted} aria-describedby="permission-intent-reason" onClick={() => permissions.submit('approved')}>Approve this request</button>
                <button type="button" disabled={!snapshot.submitDecision || permissions.pending || permissions.submitted} aria-describedby="permission-intent-reason" onClick={() => permissions.submit('denied')}>Deny this request</button>
              </div>
            </div>}
            <p role="status" aria-live="polite" aria-atomic="true">{permissions.feedback}</p>
          </>}
          {permissions.pending && <p role="status">A decision intent is still pending. It cannot be cancelled or duplicated here.</p>}
        </section>
      </div>
    </section>
  );
}

export const memoryClasses = Object.freeze(['working', 'episodic', 'semantic', 'user', 'project', 'agent'] as const);
export const fileKinds = Object.freeze(['source_document', 'generated_artifact'] as const);
export type MemoryPresentationRecord = {
  readonly id: string; readonly type: (typeof memoryClasses)[number];
  readonly content?: string; readonly sourceType?: string; readonly sourceRef?: string; readonly scope?: string;
  readonly confidence?: number; readonly importance?: number; readonly createdAt?: string; readonly updatedAt?: string;
  readonly lastAccessedAt?: string; readonly expiresAt?: string; readonly embeddingRef?: string;
};
export type FilePresentationRecord = {
  readonly id: string; readonly kind: (typeof fileKinds)[number]; readonly name: string;
  readonly format?: string; readonly mediaType?: string; readonly sourceType?: string; readonly sourceRef?: string;
  readonly contentHash?: string; readonly timestamp?: string; readonly sizeBytes?: number;
};
export type MemoryPort = { readonly status: 'connected' | 'unavailable' | 'degraded'; readonly records: readonly MemoryPresentationRecord[] };
export type FilePort = { readonly status: 'connected' | 'unavailable' | 'degraded'; readonly items: readonly FilePresentationRecord[] };
type EvidenceRecord = MemoryPresentationRecord | FilePresentationRecord;
type EvidenceSnapshot<T extends EvidenceRecord> = { status: MemoryPort['status']; entries: readonly T[]; rejected: boolean };

function normalizedEvidenceText(value: unknown, maximum: number): value is string {
  return taskText(value, maximum) && value === value.trim();
}

function evidenceUtcKey(value: string) {
  // Called only after strict UTC admission. Fixed-width keys preserve fractional
  // ordering across Z/+00:00 spellings, without a clock or fabricated local time.
  const fraction = value.match(/\.(\d+)/)?.[1] ?? '';
  return value.slice(0, 19) + fraction.padEnd(43, '0');
}

function readMemoryRecord(value: unknown): MemoryPresentationRecord | undefined {
  const fields = agentDataFields(value, ['id', 'type'], ['content', 'sourceType', 'sourceRef', 'scope', 'confidence', 'importance', 'createdAt', 'updatedAt', 'lastAccessedAt', 'expiresAt', 'embeddingRef']);
  if (!fields || !normalizedEvidenceText(fields.id, 128) || !memoryClasses.some((kind) => kind === fields.type)) return undefined;
  if ('content' in fields && !taskText(fields.content, 4000)) return undefined;
  for (const [key, maximum] of [['sourceType', 128], ['sourceRef', 512], ['scope', 256], ['embeddingRef', 128]] as const) {
    if (key in fields && !normalizedEvidenceText(fields[key], maximum)) return undefined;
  }
  for (const key of ['confidence', 'importance']) {
    const score = fields[key];
    if (key in fields && (typeof score !== 'number' || !Number.isFinite(score) || score < 0 || score > 1)) return undefined;
  }
  for (const key of ['createdAt', 'updatedAt', 'lastAccessedAt', 'expiresAt']) {
    if (key in fields && !permissionUtc(fields[key])) return undefined;
  }
  if (typeof fields.createdAt === 'string') {
    for (const key of ['updatedAt', 'lastAccessedAt', 'expiresAt']) {
      const timestamp = fields[key];
      if (typeof timestamp === 'string' && evidenceUtcKey(timestamp) < evidenceUtcKey(fields.createdAt)) return undefined;
    }
  }
  // Presentation-only, primitive copy after exact admission; not MemoryRecord
  // reconstruction, admission policy, persistence, retrieval or expiry handling.
  return Object.freeze({ ...fields }) as MemoryPresentationRecord;
}

function readFileRecord(value: unknown): FilePresentationRecord | undefined {
  const fields = agentDataFields(value, ['id', 'kind', 'name'], ['format', 'mediaType', 'sourceType', 'sourceRef', 'contentHash', 'timestamp', 'sizeBytes']);
  if (!fields || !normalizedEvidenceText(fields.id, 128) || !fileKinds.some((kind) => kind === fields.kind) || !taskText(fields.name, 256)) return undefined;
  for (const key of ['format', 'mediaType', 'sourceType', 'sourceRef', 'contentHash']) {
    if (key in fields && !taskText(fields[key], key === 'sourceRef' ? 512 : 128)) return undefined;
  }
  if ('timestamp' in fields && !permissionUtc(fields.timestamp)) return undefined;
  if ('sizeBytes' in fields && (typeof fields.sizeBytes !== 'number' || !Number.isSafeInteger(fields.sizeBytes) || fields.sizeBytes < 0)) return undefined;
  return Object.freeze({ ...fields }) as FilePresentationRecord;
}

function readEvidencePort(value: unknown, kind: 'memory'): EvidenceSnapshot<MemoryPresentationRecord>;
function readEvidencePort(value: unknown, kind: 'files'): EvidenceSnapshot<FilePresentationRecord>;
function readEvidencePort(value: unknown, kind: 'memory' | 'files'): EvidenceSnapshot<EvidenceRecord> {
  const unavailable = Object.freeze({ status: 'unavailable' as MemoryPort['status'], entries: Object.freeze([]) as readonly EvidenceRecord[], rejected: value !== undefined });
  try {
    const listKey = kind === 'memory' ? 'records' : 'items';
    const fields = agentDataFields(value, ['status', listKey]);
    if (!fields || (fields.status !== 'connected' && fields.status !== 'unavailable' && fields.status !== 'degraded')) return unavailable;
    const values = taskArray(fields[listKey], 64);
    if (!values) return unavailable;
    const entries: EvidenceRecord[] = [];
    for (const value of values) {
      const entry = kind === 'memory' ? readMemoryRecord(value) : readFileRecord(value);
      if (!entry || entries.some((item) => item.id === entry.id)) return unavailable;
      entries.push(entry);
    }
    return Object.freeze({ status: fields.status, entries: Object.freeze(fields.status === 'connected' ? entries : []), rejected: false });
  } catch {
    return unavailable;
  }
}
export function readMemoryPort(value: unknown) { return readEvidencePort(value, 'memory'); }
export function readFilePort(value: unknown) { return readEvidencePort(value, 'files'); }

export function filterEvidence<T extends EvidenceRecord>(entries: readonly T[], value: unknown) {
  const query = boundedAgentQuery(value).toLowerCase();
  // Entries are detached admitted primitives, never raw caller objects. Numeric
  // display evidence participates without coercing arbitrary values or ranking.
  return entries.filter((entry) => Object.values(entry).some((field) =>
    typeof field === 'string' ? field.toLowerCase().includes(query) : typeof field === 'number' && `${field}`.includes(query)));
}

function useEvidenceBrowser<T extends EvidenceRecord>(snapshot: EvidenceSnapshot<T>) {
  const [query, setEvidenceQuery] = useState('');
  const [selectedId, setEvidenceId] = useState<string | undefined>(undefined);
  const selected = snapshot.entries.find((entry) => entry.id === selectedId) ?? snapshot.entries[0];
  if (selectedId !== selected?.id) setEvidenceId(selected?.id);
  function search(value: unknown) { setEvidenceQuery(boundedAgentQuery(value)); }
  function select(value: unknown) {
    const entry = snapshot.entries.find((item) => item.id === value);
    if (entry) setEvidenceId(entry.id);
  }
  return { snapshot, query, selected, search, select };
}
type EvidenceBrowser<T extends EvidenceRecord> = ReturnType<typeof useEvidenceBrowser<T>>;

function EvidenceConnection({ snapshot, noun }: { snapshot: EvidenceSnapshot<EvidenceRecord>; noun: 'memory' | 'file' }) {
  return <>
    <p>{snapshot.status === 'connected' ? 'Caller-supplied evidence only. Live backend and storage are not connected.' :
      snapshot.status === 'degraded' ? `${noun === 'memory' ? 'Memory' : 'File'} source degraded. Supplied items are hidden.` : `No ${noun} source connected.`}</p>
    {snapshot.rejected && <p>Snapshot rejected because it is malformed or exceeds display limits. No items are shown.</p>}
  </>;
}

function EvidenceSearch({ browser, prefix, label }: { browser: EvidenceBrowser<EvidenceRecord>; prefix: 'memory' | 'file'; label: string }) {
  const matches = filterEvidence(browser.snapshot.entries, browser.query);
  return <section className="evidence-search" aria-label={label}>
    <label htmlFor={`${prefix}-query`}>{label}</label>
    <p id={`${prefix}-search-help`}>Local text search only, up to 80 characters.</p>
    <div className="evidence-search-controls">
      <input id={`${prefix}-query`} type="search" maxLength={80} value={browser.query} aria-describedby={`${prefix}-search-help ${prefix}-match-count`}
        onChange={(event) => browser.search(event.currentTarget.value)} />
      <button type="button" onClick={() => browser.search('')}>Clear search</button>
    </div>
    <p id={`${prefix}-match-count`} role="status" aria-live="polite" aria-atomic="true">{matches.length} of {browser.snapshot.entries.length} supplied items match</p>
  </section>;
}

export function MemoryWorkspace({ memory }: { memory: EvidenceBrowser<MemoryPresentationRecord> }) {
  const matches = filterEvidence(memory.snapshot.entries, memory.query);
  const selected = memory.selected;
  return <section className="evidence-workspace" aria-labelledby="memory-heading">
    <header className="evidence-header">
      <p className="eyebrow">Read-only · memory evidence</p><h3 id="memory-heading">Memory records</h3>
      <p>PUDDING owns memory. Reviewing supplied content does not admit, persist or verify it. Content and provenance are untrusted data.</p>
      <EvidenceConnection snapshot={memory.snapshot} noun="memory" />
    </header>
    <EvidenceSearch browser={memory} prefix="memory" label="Search memory evidence" />
    <div className="evidence-layout">
      <section className="evidence-results" aria-label="Matching memory records">
        {memory.snapshot.entries.length === 0 ? <p>No memory records supplied.</p> : matches.length === 0 ? <p>No records match. Clear search to restore supplied records.</p> :
          <ul className="evidence-list">{matches.map((entry) => <li key={entry.id}><button className="evidence-card" type="button" aria-pressed={selected?.id === entry.id} aria-controls="memory-detail" onClick={() => memory.select(entry.id)}>
            <span className="evidence-id">{entry.id}</span><span>Memory class: {entry.type}</span><span>Scope: {entry.scope ?? 'Not reported'}</span>
          </button></li>)}</ul>}
      </section>
      <section className="evidence-detail" id="memory-detail" aria-labelledby="memory-detail-heading">
        <h4 id="memory-detail-heading">Selected memory evidence</h4>
        {!selected ? <p>No admitted memory record selected.</p> : <>
          <p role="status" aria-live="polite" aria-atomic="true">Selected record: {selected.id}{!matches.some((entry) => entry.id === selected.id) ? ' — outside the current filter. Clear search to show its selection button.' : ''}</p>
          <dl><dt>Record ID</dt><dd>{selected.id}</dd><dt>Memory class</dt><dd>{selected.type}</dd>
            <dt>Content (untrusted text)</dt><dd className="evidence-text">{selected.content ?? 'Not reported'}</dd>
            <dt>Source type</dt><dd>{selected.sourceType ?? 'Not reported'}</dd><dt>Source reference (inert text)</dt><dd className="evidence-text">{selected.sourceRef ?? 'Not reported'}</dd>
            <dt>Scope</dt><dd>{selected.scope ?? 'Not reported'}</dd><dt>Supplied confidence</dt><dd>{selected.confidence ?? 'Not reported'}</dd><dt>Supplied importance</dt><dd>{selected.importance ?? 'Not reported'}</dd>
            <dt>Created at (supplied UTC)</dt><dd>{selected.createdAt ?? 'Not reported'}</dd><dt>Updated at (supplied UTC)</dt><dd>{selected.updatedAt ?? 'Not reported'}</dd>
            <dt>Last accessed at (supplied UTC)</dt><dd>{selected.lastAccessedAt ?? 'Not reported'}</dd><dt>Expiry (supplied UTC, no expiry status inferred)</dt><dd>{selected.expiresAt ?? 'Not reported'}</dd>
            <dt>Embedding reference (opaque evidence)</dt><dd className="evidence-text">{selected.embeddingRef ?? 'Not reported'}</dd>
          </dl>
          <p>These fields do not prove authoritative memory, source truth, persistence or retrieval. This view cannot create, replace, delete or forget memory.</p>
        </>}
      </section>
    </div>
  </section>;
}

export function FilesWorkspace({ files }: { files: EvidenceBrowser<FilePresentationRecord> }) {
  const matches = filterEvidence(files.snapshot.entries, files.query);
  const selected = files.selected;
  return <section className="evidence-workspace" aria-labelledby="file-heading">
    <header className="evidence-header">
      <p className="eyebrow">Read-only · document metadata</p><h3 id="file-heading">Files / Documents</h3>
      <p>Source documents and generated artifacts are distinct supplied evidence. ROBIN owns generation; VEGAPUNK owns indexing. No file existence, readability, indexing or storage is verified here.</p>
      <EvidenceConnection snapshot={files.snapshot} noun="file" />
    </header>
    <EvidenceSearch browser={files} prefix="file" label="Search file metadata" />
    <div className="evidence-layout">
      <section className="evidence-results" aria-label="Matching file metadata">
        {files.snapshot.entries.length === 0 ? <p>No file metadata supplied.</p> : matches.length === 0 ? <p>No items match. Clear search to restore supplied items.</p> :
          <ul className="evidence-list">{matches.map((entry) => <li key={entry.id}><button className="evidence-card" type="button" aria-pressed={selected?.id === entry.id} aria-controls="file-detail" onClick={() => files.select(entry.id)}>
            <span className="evidence-id">{entry.name}</span><span>{entry.kind === 'generated_artifact' ? 'Generated artifact' : 'Source document'}</span><span>ID: {entry.id}</span>
          </button></li>)}</ul>}
      </section>
      <section className="evidence-detail" id="file-detail" aria-labelledby="file-detail-heading">
        <h4 id="file-detail-heading">Selected document / artifact metadata</h4>
        {!selected ? <p>No admitted item selected.</p> : <>
          <p role="status" aria-live="polite" aria-atomic="true">Selected item: {selected.id}{!matches.some((entry) => entry.id === selected.id) ? ' — outside the current filter. Clear search to show its selection button.' : ''}</p>
          <dl><dt>Item ID</dt><dd>{selected.id}</dd><dt>Kind</dt><dd>{selected.kind}</dd><dt>Display name / title (untrusted text)</dt><dd className="evidence-text">{selected.name}</dd>
            <dt>Supplied format</dt><dd>{selected.format ?? 'Not reported'}</dd><dt>Supplied media type</dt><dd>{selected.mediaType ?? 'Not reported'}</dd>
            <dt>Source type</dt><dd>{selected.sourceType ?? 'Not reported'}</dd><dt>Source reference (inert text)</dt><dd className="evidence-text">{selected.sourceRef ?? 'Not reported'}</dd>
            <dt>Supplied content hash (not independently verified)</dt><dd className="evidence-text">{selected.contentHash ?? 'Not reported'}</dd>
            <dt>Timestamp (supplied UTC)</dt><dd>{selected.timestamp ?? 'Not reported'}</dd><dt>Supplied byte size</dt><dd>{selected.sizeBytes ?? 'Not reported'}</dd>
          </dl>
          {selected.kind === 'generated_artifact' && <p>A generated artifact may exist only in memory. Supplied metadata is not proof that it was saved to disk.</p>}
          <p>Names, references, formats and hashes are inert text. No open, read, upload, download, save, export, delete, parsing or indexing action is connected.</p>
        </>}
      </section>
    </div>
  </section>;
}

export function ViewPanel({ view, chatPort, chat, agentPort, directory, task, permissions, memory, files }: { view: ViewDefinition; chatPort?: ChatPort; chat?: ChatDraft; agentPort?: AgentPort; directory?: AgentDirectory; task?: TaskPresentation; permissions?: PermissionPresentation; memory?: EvidenceBrowser<MemoryPresentationRecord>; files?: EvidenceBrowser<FilePresentationRecord> }) {
  if (view.id === 'home') {
    return chat ? <ChatWorkspace chatPort={chatPort} chat={chat} /> : null;
  }
  if (view.id === 'agents') return directory ? <AgentNetwork agentPort={agentPort} directory={directory} /> : null;
  if (view.id === 'tasks') return task ? <TaskWorkspace task={task} /> : null;
  if (view.id === 'permissions') return permissions ? <PermissionCenter permissions={permissions} /> : null;
  if (view.id === 'memory') return memory ? <MemoryWorkspace memory={memory} /> : null;
  if (view.id === 'files') return files ? <FilesWorkspace files={files} /> : null;
  return (
    <section className="placeholder-panel" aria-labelledby="workspace-heading">
      <p className="eyebrow">Workspace preview</p>
      <h3 id="workspace-heading">{view.label} workspace</h3>
      <p>{view.purpose}</p>
      <div className="placeholder-note">
        <strong>This workspace is not connected yet.</strong>
        <p>Live information and actions will appear when this workspace is integrated. Navigation remains available throughout the app.</p>
      </div>
    </section>
  );
}

export default function App({ chatPort, agentPort, taskPort, permissionPort, memoryPort, filePort }: { chatPort?: ChatPort; agentPort?: AgentPort; taskPort?: TaskGraphPort; permissionPort?: PermissionPort; memoryPort?: MemoryPort; filePort?: FilePort } = {}) {
  const [selectedView, setSelectedView] = useState<ViewId>('home');
  const mainRef = useRef<HTMLElement>(null);
  const focusRequested = useRef(false);
  const currentView = resolveView(selectedView);
  // App owns the draft and in-flight guard so navigation cannot reset a pending send.
  const chat = useChatDraft(chatPort);
  const directory = useAgentDirectory();
  const task = useTaskGraph(taskPort);
  const permissions = usePermissionCenter(permissionPort);
  const memory = useEvidenceBrowser(readMemoryPort(memoryPort));
  const files = useEvidenceBrowser(readFilePort(filePort));

  useEffect(() => {
    if (focusRequested.current) {
      mainRef.current?.focus();
      focusRequested.current = false;
    }
  }, [selectedView]);

  function navigate(value: unknown) {
    const nextView = resolveView(value).id;
    if (nextView === selectedView) {
      mainRef.current?.focus();
      return;
    }
    focusRequested.current = true;
    setSelectedView(nextView);
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <aside className="sidebar" aria-label="PHOENIX workspace navigation">
        <header className="brand">
          <span className="brand-mark" aria-hidden="true">P</span>
          <div><h1>PHOENIX</h1><p className="tagline">Rise from the ashes.</p></div>
        </header>
        <nav aria-label="Primary navigation">
          {groups.map((group) => (
            <section className="nav-group" key={group} aria-label={group}>
              <h2 className="nav-group-title">{group}</h2>
              <ul>
                {views.filter((view) => view.group === group).map((view) => (
                  <li key={view.id}>
                    <button
                      type="button"
                      className="nav-button"
                      aria-current={currentView.id === view.id ? 'page' : undefined}
                      onClick={() => navigate(view.id)}
                    >{view.label}</button>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </nav>
        <p className="sidebar-note">Local-first by design.<br />One interface. Your direction.</p>
      </aside>
      <main id="main-content" ref={mainRef} tabIndex={-1} aria-labelledby="page-title">
        <header className="page-header">
          <p className="integration-status">Interface shell — backend integration pending</p>
          <p className="eyebrow">{currentView.group}</p>
          <h2 id="page-title">{currentView.label}</h2>
          <p className="page-purpose">{currentView.purpose}</p>
        </header>
        <div className="page-content"><ViewPanel view={currentView} chatPort={chatPort} chat={chat} agentPort={agentPort} directory={directory} task={task} permissions={permissions} memory={memory} files={files} /></div>
      </main>
    </div>
  );
}
