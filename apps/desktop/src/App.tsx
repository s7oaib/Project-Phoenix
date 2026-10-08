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

export function ViewPanel({ view, chatPort, chat, agentPort, directory }: { view: ViewDefinition; chatPort?: ChatPort; chat?: ChatDraft; agentPort?: AgentPort; directory?: AgentDirectory }) {
  if (view.id === 'home') {
    return chat ? <ChatWorkspace chatPort={chatPort} chat={chat} /> : null;
  }
  if (view.id === 'agents') return directory ? <AgentNetwork agentPort={agentPort} directory={directory} /> : null;
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

export default function App({ chatPort, agentPort }: { chatPort?: ChatPort; agentPort?: AgentPort } = {}) {
  const [selectedView, setSelectedView] = useState<ViewId>('home');
  const mainRef = useRef<HTMLElement>(null);
  const focusRequested = useRef(false);
  const currentView = resolveView(selectedView);
  // App owns the draft and in-flight guard so navigation cannot reset a pending send.
  const chat = useChatDraft(chatPort);
  const directory = useAgentDirectory();

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
        <div className="page-content"><ViewPanel view={currentView} chatPort={chatPort} chat={chat} agentPort={agentPort} directory={directory} /></div>
      </main>
    </div>
  );
}
