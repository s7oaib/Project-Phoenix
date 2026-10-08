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

export function ViewPanel({ view, chatPort, chat }: { view: ViewDefinition; chatPort?: ChatPort; chat?: ChatDraft }) {
  if (view.id === 'home') {
    return chat ? <ChatWorkspace chatPort={chatPort} chat={chat} /> : null;
  }
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

export default function App({ chatPort }: { chatPort?: ChatPort } = {}) {
  const [selectedView, setSelectedView] = useState<ViewId>('home');
  const mainRef = useRef<HTMLElement>(null);
  const focusRequested = useRef(false);
  const currentView = resolveView(selectedView);
  // App owns the draft and in-flight guard so navigation cannot reset a pending send.
  const chat = useChatDraft(chatPort);

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
        <div className="page-content"><ViewPanel view={currentView} chatPort={chatPort} chat={chat} /></div>
      </main>
    </div>
  );
}
