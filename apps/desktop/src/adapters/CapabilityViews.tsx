// PHX-122 (ADR-170 to ADR-174): the desktop views for capabilities the backend already serves:
// the voice mic toggle and live state, the user's uploads and memory notes, and the display-only
// approval summary above the Permissions cards.
//
// Presentation only: every value comes from the store's backend state and every action is a
// store method that calls an allowlisted route through the native proxy. Voice never approves a
// permission, and nothing here decides one; PermissionEngine stays authoritative.

import { useState, type ChangeEvent, type FormEvent } from 'react';
import {
  MAX_MEMORY_CHARS, MAX_UPLOAD_BYTES, VOICE_ACTIVE, type ApprovalSummaryView, type ConnectionView, type PendingPermission, type PhoenixStore,
  type VoiceState,
} from './core';

export type VoiceStore = Pick<PhoenixStore, 'setVoice'>;
export type KnowledgeStore = Pick<PhoenixStore, 'uploadFile' | 'deleteFile' | 'addMemory' | 'deleteMemory' | 'searchMemory'>;

const voiceStateText: Readonly<Record<VoiceState, string>> = {
  off: 'Off — the microphone is closed.',
  starting: 'Starting — loading the voice models…',
  sleeping: 'Waiting for the wake phrase.',
  listening: 'Listening…',
  thinking: 'Thinking…',
  speaking: 'Speaking…',
  degraded_voice: 'Voice degraded — some voice components are unavailable.',
  text_only: 'Text only — voice output is unavailable; answers appear in Chat.',
  unavailable: 'Voice unavailable.',
};
const voiceCodeText: Readonly<Record<string, string>> = {
  models_missing: 'The voice models are not installed.',
  dependency_unavailable: 'A voice dependency is not installed.',
  microphone_unavailable: 'No microphone is available.',
  voice_failure: 'The voice pipeline failed.',
};

/** True when voice cannot take spoken requests: chat is the way in. */
export function voiceFallback(state: VoiceState | undefined): boolean {
  return state === undefined || state === 'unavailable' || state === 'text_only';
}

export function VoiceControl({ connection, store, detailed = false }: { connection: ConnectionView; store: VoiceStore; detailed?: boolean }) {
  const voice = connection.voice;
  const ready = connection.backend.state === 'ready';
  const on = voice !== undefined && VOICE_ACTIVE.includes(voice.state);
  const state = voice ? `${voiceStateText[voice.state]}${connection.voiceInterrupted ? ' (interrupted — you spoke over the answer)' : ''}` : 'Voice status unavailable.';
  return (
    <section className="capability-card voice-control" aria-labelledby={detailed ? 'voice-control-heading' : 'voice-bar-heading'}>
      <h3 id={detailed ? 'voice-control-heading' : 'voice-bar-heading'}>Voice</h3>
      <p className={`voice-state voice-${voice?.state ?? 'unknown'}`} role="status" aria-live="polite">{state}{voice?.code ? ` ${voiceCodeText[voice.code] ?? voice.code}` : ''}</p>
      <div className="capability-actions">
        <button type="button" aria-pressed={on} disabled={!ready || !voice || connection.voiceBusy} onClick={() => void store.setVoice(!on)}>
          {connection.voiceBusy ? 'Working…' : on ? 'Stop listening (mic off)' : 'Start listening (mic on)'}
        </button>
      </div>
      {voice && on && <p>Say “{voice.wakePhrase}”, then your request. Spoken requests become normal missions in Chat.</p>}
      {voiceFallback(voice?.state) && <p className="capability-note">Text fallback: type your request in Chat — everything works without voice.</p>}
      <p className="capability-note">Voice never approves permissions. Approve or deny each request yourself in Permissions.</p>
      <p className="capability-feedback" role="status" aria-live="polite">{connection.voiceMessage}</p>
      {detailed && voice && voice.components.length > 0 &&
        <ul className="setup-list">{voice.components.map((component) => <li key={component.id}>{component.id.replace('_', ' ')}: {component.status}</li>)}</ul>}
    </section>
  );
}

function mebibytes(bytes: number): string {
  return `${(bytes / 1024 ** 2).toFixed(bytes < 1024 ** 2 ? 2 : 1)} MiB`;
}

/** The file's bytes as standard base64 (the data URL's payload), read in the webview only. */
export function readFileBase64(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => { const url = String(reader.result); resolve(url.slice(url.indexOf(',') + 1)); };
    reader.onerror = () => reject(new Error('read_failed'));
    reader.readAsDataURL(file);
  });
}

export function FilesPanel({ connection, store }: { connection: ConnectionView; store: KnowledgeStore }) {
  const [file, setFile] = useState<File | undefined>(undefined);
  const ready = connection.backend.state === 'ready';
  const tooLarge = file !== undefined && file.size > MAX_UPLOAD_BYTES;
  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || tooLarge) return;
    const content = await readFileBase64(file).catch(() => '');
    await store.uploadFile(file.name, content ? file.size : 0, content);
  }
  return (
    <section className="setup-panel" aria-labelledby="files-heading">
      <h3 id="files-heading">Files / Documents</h3>
      <p>Upload a document so PHOENIX can search it and cite it in answers. Limit: {mebibytes(MAX_UPLOAD_BYTES)} per file. Images are not indexed.</p>
      <form className="setup-form" onSubmit={upload}>
        <label htmlFor="upload-file">Document to upload (up to 8 MiB)</label>
        <input id="upload-file" type="file" disabled={!ready} aria-describedby="upload-feedback"
          onChange={(event: ChangeEvent<HTMLInputElement>) => setFile(event.currentTarget.files?.[0])} />
        <button type="submit" disabled={!ready || !file || tooLarge || connection.knowledgeBusy}>{connection.knowledgeBusy ? 'Working…' : 'Upload'}</button>
        {tooLarge && <p>{file.name} is {mebibytes(file.size)}; the limit is 8 MiB.</p>}
        <p id="upload-feedback" className="setup-feedback" role="status" aria-live="polite">{connection.knowledgeMessage}</p>
      </form>
      <h4>Your uploads</h4>
      {!ready || connection.documents === undefined ? <p>Uploads are unavailable until the local backend is connected.</p> :
        connection.documents.length === 0 ? <p>No uploads yet.</p> :
          <ul className="capability-list">{connection.documents.map((document) => (
            <li key={document.documentId}>
              <span>{document.filename} · {document.mediaType} · {document.chunkCount} indexed chunk(s) · {document.indexedAt}</span>
              <button type="button" disabled={connection.knowledgeBusy} onClick={() => void store.deleteFile(document.documentId)}>Delete {document.filename}</button>
            </li>))}
          </ul>}
    </section>
  );
}

export function MemoryPanel({ connection, store }: { connection: ConnectionView; store: KnowledgeStore }) {
  const [note, setNote] = useState('');
  const [query, setQuery] = useState('');
  const ready = connection.backend.state === 'ready';
  const busy = !ready || connection.knowledgeBusy;
  return (
    <section className="setup-panel" aria-labelledby="memory-panel-heading">
      <h3 id="memory-panel-heading">Memory</h3>
      <p>Notes you save here are what PHOENIX remembers about you. Only you write memory; missions never do. Text that looks like a credential is refused.</p>
      <form className="setup-form" onSubmit={(event) => { event.preventDefault(); void store.addMemory(note).then(() => setNote('')); }}>
        <label htmlFor="memory-note">New note (up to {MAX_MEMORY_CHARS} characters)</label>
        <textarea id="memory-note" rows={3} maxLength={MAX_MEMORY_CHARS} value={note} onChange={(event) => setNote(event.currentTarget.value)} />
        <button type="submit" disabled={busy || !note.trim()}>Remember</button>
      </form>
      <form className="setup-form" role="search" onSubmit={(event) => { event.preventDefault(); void store.searchMemory(query); }}>
        <label htmlFor="memory-search">Search notes and uploads</label>
        <input id="memory-search" type="search" maxLength={200} value={query} onChange={(event) => setQuery(event.currentTarget.value)} />
        <button type="submit" disabled={busy || !query.trim()}>Search</button>
      </form>
      <p className="setup-feedback" role="status" aria-live="polite">{connection.knowledgeMessage}</p>
      {connection.searchHits && connection.searchHits.length > 0 &&
        <ul className="capability-list" aria-label="Search results">{connection.searchHits.map((hit, index) => (
          <li key={index}><span>{hit.kind === 'memory' ? 'Note' : `Upload${hit.title ? ` ${hit.title}` : ''}`}: {hit.text}</span></li>))}
        </ul>}
      <h4>Saved notes</h4>
      {!ready || connection.memories === undefined ? <p>Memory is unavailable until the local backend is connected.</p> :
        connection.memories.length === 0 ? <p>No notes yet.</p> :
          <ul className="capability-list">{connection.memories.map((memory) => (
            <li key={memory.memoryId}>
              <span>{memory.content} <small>({memory.memoryType} · {memory.createdAt})</small></span>
              <button type="button" disabled={connection.knowledgeBusy} onClick={() => void store.deleteMemory(memory.memoryId)}>Delete note</button>
            </li>))}
          </ul>}
    </section>
  );
}

function SummaryFields({ summary }: { summary: ApprovalSummaryView }) {
  const process = [summary.processName, summary.executablePath, summary.processId === null ? null : `PID ${summary.processId}`].filter(Boolean).join(' · ');
  const rows: Array<[string, string | null]> = [
    ['Page', summary.pageUrl], ['Element', summary.element], ['Window', summary.windowTitle], ['Program', process || null],
    ['Command', summary.command], ['Text to type', summary.textMasked ? '[hidden]' : summary.text], ['Note', summary.note],
  ];
  return <dl className="approval-fields">{rows.filter(([, value]) => value !== null).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}

/** What each pending computer-control request would do, in plain words, above App's cards with
 *  their raw tool, action and resource. Display only: the decision binds to the stored request. */
export function ApprovalSummaries({ approvals }: { approvals: readonly PendingPermission[] }) {
  const shown = approvals.filter((request) => request.summary || request.riskLevel === 'P4');
  if (shown.length === 0) return null;
  return (
    <section className="capability-card approval-summaries" aria-labelledby="approval-summaries-heading">
      <h3 id="approval-summaries-heading">What PHOENIX is asking to do</h3>
      <ul className="capability-list">{shown.map((request) => (
        <li key={request.requestId} className="approval-summary">
          <p><strong>{request.summary?.action ?? request.action}</strong> <span className="capability-note">(request {request.requestId.slice(0, 8)})</span></p>
          {request.riskLevel === 'P4' && <p className="approval-once">One-time approval (P4): approving allows this exact action once. Nothing is remembered; PHOENIX asks again next time.</p>}
          {request.summary && <SummaryFields summary={request.summary} />}
        </li>))}
      </ul>
      <p className="capability-note">These details describe the request; the raw tool, action and resource are in each card below. Approve or deny there.</p>
    </section>
  );
}
