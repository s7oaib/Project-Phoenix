// PHX-107 (ADR-092) + minimal PHX-108: renders App with live ports and a compact connection
// bar for what App has no port for yet: backend state, the document workspace folder
// (PATCH /settings), the mission path and the active mission's cancel / run-again.

import { useState, useSyncExternalStore, type FormEvent } from 'react';
import App from '../App';
import type { ConnectionView, MissionPath, PhoenixStore } from './core';
import './connected.css';

const backendText: Readonly<Record<string, string>> = {
  starting: 'Starting the local PHOENIX backend…',
  ready: 'Connected to the local PHOENIX backend through the native shell.',
  degraded: 'Backend unavailable.',
};
const degradedText: Readonly<Record<string, string>> = {
  backend_not_found: 'The PHOENIX repository was not found (set PHOENIX_BACKEND_ROOT).',
  python_not_found: 'The backend Python environment was not found (run uv sync, or set PHOENIX_BACKEND_PYTHON).',
  data_dir_unavailable: 'The data directory cannot be used.',
  already_running: 'Another PHOENIX desktop already runs a backend on this data directory.',
  spawn_failed: 'The backend process could not be started.',
  credential_failed: 'A launch credential could not be generated.',
  ready_timeout: 'The backend did not become ready in time.',
  backend_exited: 'The backend process exited. Restart PHOENIX.',
  ipc_unavailable: 'The native shell did not answer.',
};
const missionText: Readonly<Record<string, string>> = {
  queued: 'queued', running: 'running', completed: 'completed', failed: 'failed', cancelled: 'cancelled',
  waiting_permission: 'waiting for your permission — open Permissions to approve or deny', superseded: 'superseded by a linked re-run',
};

export function ConnectionBar({ connection, store }: { connection: ConnectionView; store: Pick<PhoenixStore, 'setWorkspace' | 'setPath' | 'missionAction'> }) {
  const [folder, setFolder] = useState('');
  const [busy, setBusy] = useState(false);
  const { backend, stream, workspace, mission } = connection;
  const ready = backend.state === 'ready';

  async function saveWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    try { await store.setWorkspace(folder); } finally { setBusy(false); }
  }

  return (
    <section className="connection-bar" aria-label="Backend connection">
      <p className={`connection-state connection-${backend.state}`} role="status" aria-live="polite">
        {backendText[backend.state]}{backend.state === 'degraded' && backend.code ? ` ${degradedText[backend.code] ?? backend.code}` : ''}
        {ready ? (stream.connected ? ' Live progress connected.' : ' Live progress reconnecting; views reload from the backend.') : ''}
      </p>
      {ready && <>
        <form className="connection-row" onSubmit={saveWorkspace}>
          <label htmlFor="workspace-root">Document workspace</label>
          <span className="connection-value">{workspace?.root ? `${workspace.root} (${workspace.status})` : 'Not set — document steps are off'}</span>
          <input id="workspace-root" type="text" maxLength={4096} placeholder={'D:\\Documents\\project'} value={folder}
            aria-describedby="workspace-feedback" onChange={(event) => setFolder(event.currentTarget.value)} />
          <button type="submit" disabled={busy}>Set folder</button>
          <span id="workspace-feedback" className="connection-feedback" role="status" aria-live="polite">{connection.workspaceMessage}</span>
        </form>
        <div className="connection-row">
          <label htmlFor="mission-path">Mission path</label>
          <select id="mission-path" value={connection.path} onChange={(event) => store.setPath(event.currentTarget.value as MissionPath)}>
            <option value="auto">Server default</option>
            <option value="direct">Direct answer</option>
            <option value="complex">Planned steps (AIZEN plan, workspace documents)</option>
          </select>
          {mission && <span className="connection-value">Latest mission {mission.missionId.slice(0, 8)}: {missionText[mission.status] ?? mission.status}{mission.failureCode ? ` (${mission.failureCode})` : ''}</span>}
          {mission && (mission.status === 'queued' || mission.status === 'running') &&
            <button type="button" onClick={() => void store.missionAction('cancel')}>Cancel mission</button>}
          {mission && mission.status === 'failed' && mission.failureCode === 'interrupted' && !mission.rerunId &&
            <button type="button" onClick={() => void store.missionAction('run-again')}>Run again</button>}
          <span className="connection-feedback" role="status" aria-live="polite">{connection.missionMessage}</span>
        </div>
        {connection.hiddenPermissions > 0 && <p className="connection-feedback">{connection.hiddenPermissions} pending permission request(s) cannot be shown because a field exceeds display limits.</p>}
      </>}
    </section>
  );
}

export function ConnectedApp({ store }: { store: PhoenixStore }) {
  const view = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  return (
    <>
      <ConnectionBar connection={view.connection} store={store} />
      <App chatPort={view.chatPort} agentPort={view.agentPort} taskPort={view.taskPort} permissionPort={view.permissionPort}
        modelPort={view.modelPort} observabilityPort={view.observabilityPort} />
    </>
  );
}
