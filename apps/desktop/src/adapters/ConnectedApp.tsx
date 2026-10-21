// PHX-107 (ADR-092) + PHX-108 (ADR-110 to ADR-114): renders App with live ports and a compact
// connection bar (backend state, the document workspace, this launch's mission path, and the
// active mission's cancel / run-again). While setup is incomplete, a first-run screen shows the
// onboarding steps instead of App until the user continues. App's Onboarding, Settings,
// Computer Control, Research and Media views are supplied from SetupViews.

import { useSyncExternalStore } from 'react';
import App from '../App';
import { verificationText, type ConnectionView, type MissionPath, type MissionView, type PhoenixStore } from './core';
import { CapabilityPanel, FirstRunScreen, OnboardingPanel, SettingsPanel } from './SetupViews';
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
const triageText: Readonly<Record<string, string>> = {
  decided: 'PHOENIX decided', fallback: 'triage fallback', client: 'your override',
};
/** Review M1: a completed mission always states whether LEVI verified it, in text; a direct
 *  fallback is never described as planned steps. */
export function missionLine(mission: MissionView): string {
  const status = missionText[mission.status] ?? mission.status;
  const failure = mission.failureCode ? ` (${mission.failureCode})` : '';
  const verification = mission.verification ? ` · ${verificationText[mission.verification]}` : '';
  const fallback = mission.verification === 'unverified_direct_fallback';
  const path = !mission.path || fallback || mission.verification === 'unverified_direct' ? '' :
    ` · ${mission.path === 'complex' ? 'planned steps' : 'direct'}${mission.triageOutcome ? ` (${triageText[mission.triageOutcome]})` : ''}`;
  return `Latest mission ${mission.missionId.slice(0, 8)}: ${status}${failure}${verification}${path}`;
}
const missionText: Readonly<Record<string, string>> = {
  queued: 'queued', running: 'running', completed: 'completed', failed: 'failed', cancelled: 'cancelled',
  waiting_permission: 'waiting for your permission — open Permissions to approve or deny', superseded: 'superseded by a linked re-run',
};

export function ConnectionBar({ connection, store }: { connection: ConnectionView; store: Pick<PhoenixStore, 'setPath' | 'missionAction'> }) {
  const { backend, stream, workspace, mission } = connection;
  const ready = backend.state === 'ready';

  return (
    <section className="connection-bar" aria-label="Backend connection">
      <p className={`connection-state connection-${backend.state}`} role="status" aria-live="polite">
        {backendText[backend.state]}{backend.state === 'degraded' && backend.code ? ` ${degradedText[backend.code] ?? backend.code}` : ''}
        {ready ? (stream.connected ? ' Live progress connected.' : ' Live progress reconnecting; views reload from the backend.') : ''}
      </p>
      {ready && <>
        <p className="connection-row">
          <span className="connection-label">Document workspace</span>
          <span className="connection-value">{workspace?.root ? `${workspace.root} (${workspace.status})` : 'Not set — document steps are off'} · change it in Settings</span>
        </p>
        <div className="connection-row">
          <label htmlFor="mission-path">Mission path (this launch)</label>
          <select id="mission-path" value={connection.path} onChange={(event) => store.setPath(event.currentTarget.value as MissionPath)}>
            <option value="auto">Automatic (PHOENIX decides)</option>
            <option value="direct">Override: direct answer</option>
            <option value="complex">Override: planned steps (AIZEN plan, workspace documents)</option>
          </select>
          {mission && <span className="connection-value">{missionLine(mission)}</span>}
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

export const CONNECTED_STATUS = 'Connected to the local PHOENIX backend — live data';

export function ConnectedApp({ store }: { store: PhoenixStore }) {
  const view = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  const connection = view.connection;
  const firstRun = connection.backend.state === 'ready' && connection.onboarding !== undefined && !connection.onboarding.complete && !connection.onboardingDismissed;
  const panels = {
    onboarding: <OnboardingPanel connection={connection} store={store} />,
    settings: <SettingsPanel connection={connection} store={store} />,
    computer: <CapabilityPanel view="computer" connection={connection} />,
    research: <CapabilityPanel view="research" connection={connection} />,
    media: <CapabilityPanel view="media" connection={connection} />,
  };
  return (
    <>
      <ConnectionBar connection={connection} store={store} />
      {firstRun ? <FirstRunScreen connection={connection} store={store} /> :
        <App chatPort={view.chatPort} agentPort={view.agentPort} taskPort={view.taskPort} permissionPort={view.permissionPort}
          modelPort={view.modelPort} observabilityPort={view.observabilityPort} panels={panels}
          statusLine={connection.backend.state === 'ready' ? CONNECTED_STATUS : 'Local PHOENIX backend not connected'} />}
    </>
  );
}
