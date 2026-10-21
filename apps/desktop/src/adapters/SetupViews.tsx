// PHX-108 (ADR-110 to ADR-114): first-run onboarding, the writable Settings view and the
// Computer Control, Research and Media views, rendered from the store's backend state.
//
// Presentation only: every value comes from GET /onboarding, /settings and /capabilities, and
// every action is a store method that calls an allowlisted route through the native proxy. No
// view starts, installs or downloads anything; a disabled capability says so plainly.

import { useState, type FormEvent, type ReactNode } from 'react';
import { agentRoster } from '../App';
import { missionPathText, type CapabilityView, type ConnectionView, type MissionPath, type ModelCheckView, type PhoenixStore, type RuntimeCheckView } from './core';

export type SetupStore = Pick<PhoenixStore, 'setWorkspace' | 'setDefaultPath' | 'recheck' | 'dismissOnboarding'>;

const workspaceStatusText: Readonly<Record<string, string>> = {
  active: 'active', unset: 'not set', unavailable: 'unavailable — choose it again or pick another folder',
};

function Step({ number, title, done, children }: { number: number; title: string; done: boolean; children: ReactNode }) {
  return (
    <li className="setup-step">
      <h4><span className="setup-step-number">{number}.</span> {title} <span className={done ? 'setup-done' : 'setup-todo'}>{done ? '— done' : '— action needed'}</span></h4>
      {children}
    </li>
  );
}

function runtimeLine(runtime: RuntimeCheckView): string {
  const name = runtime.displayName;
  switch (runtime.state) {
    case 'running': return `${name} is running.`;
    case 'degraded': return `${name} answers but not correctly. Restart ${name}, then select Re-check.`;
    case 'not_running': return `${name} is installed but not running. Start ${name}, then select Re-check. PHOENIX picks it up without a restart.`;
    default: return `${name} was not found. Install ${name} and start it, then select Re-check.`;
  }
}

function ModelLine({ model, runtimeRunning }: { model: ModelCheckView; runtimeRunning: boolean }) {
  const kind = model.required ? 'required' : 'optional';
  if (model.registered) return <li>{model.modelId} ({kind}) — ready</li>;
  if (!runtimeRunning) return <li>{model.modelId} ({kind}) — checked once the model runtime is running</li>;
  return (
    <li>{model.modelId} ({kind}) — not installed{model.installCommand ? <>; install it with <code>{model.installCommand}</code></> : ''}</li>
  );
}

function gigabytes(bytes: number | null): string {
  return bytes === null ? 'free space not reported' : `${(bytes / 1024 ** 3).toFixed(1)} GB free`;
}

export function WorkspaceForm({ connection, store, id }: { connection: ConnectionView; store: Pick<SetupStore, 'setWorkspace'>; id: string }) {
  const [folder, setFolder] = useState('');
  const [busy, setBusy] = useState(false);
  const workspace = connection.workspace;
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    try { await store.setWorkspace(folder); } finally { setBusy(false); }
  }
  return (
    <form className="setup-form" onSubmit={save}>
      <p>Current folder: {workspace?.root ? `${workspace.root} (${workspaceStatusText[workspace.status] ?? workspace.status})` : 'not set — document steps are off'}</p>
      <label htmlFor={id}>Document folder (absolute path)</label>
      <input id={id} type="text" maxLength={4096} placeholder={'D:\\Documents\\project'} value={folder}
        aria-describedby={`${id}-feedback`} onChange={(event) => setFolder(event.currentTarget.value)} />
      <button type="submit" disabled={busy}>Set folder</button>
      <p id={`${id}-feedback`} className="setup-feedback" role="status" aria-live="polite">{connection.workspaceMessage}</p>
    </form>
  );
}

/** The four setup steps, in the order the user must do them. Used by the first-run screen and
 *  by the Onboarding view. */
export function OnboardingPanel({ connection, store, firstRun = false }: { connection: ConnectionView; store: SetupStore; firstRun?: boolean }) {
  const onboarding = connection.onboarding;
  if (connection.backend.state !== 'ready' || !onboarding) {
    return (
      <section className="setup-panel" aria-labelledby="onboarding-heading">
        <h3 id="onboarding-heading">Set up PHOENIX</h3>
        <p>Setup status is unavailable until the local backend is connected.</p>
      </section>
    );
  }
  const runtimes = onboarding.runtimes;
  const running = runtimes.length > 0 && runtimes.every((runtime) => runtime.state === 'running');
  const required = onboarding.models.filter((model) => model.required);
  const modelsReady = required.every((model) => model.registered);
  const storageReady = onboarding.storage.databaseReady;
  const workspaceReady = onboarding.workspace.status === 'active';
  return (
    <section className="setup-panel" aria-labelledby="onboarding-heading">
      <h3 id="onboarding-heading">{firstRun ? 'Welcome — set up PHOENIX' : 'Set up PHOENIX'}</h3>
      <p role="status" aria-live="polite">{onboarding.complete ? 'Setup is complete. Every check passed.' : 'Complete these steps in order. Each check reflects the backend right now.'}</p>
      <ol className="setup-steps">
        <Step number={1} title="Start the local model runtime" done={running}>
          {runtimes.length === 0 ? <p>No model runtime is configured.</p> : runtimes.map((runtime) => <p key={runtime.providerId}>{runtimeLine(runtime)}</p>)}
        </Step>
        <Step number={2} title="Install the required models" done={running && modelsReady}>
          <ul className="setup-list">{onboarding.models.map((model) => <ModelLine key={`${model.providerId}/${model.modelId}`} model={model} runtimeRunning={running} />)}</ul>
          <p>Required models run PHOENIX's answers, planning and verification; optional ones are used when present.</p>
        </Step>
        <Step number={3} title="Check data storage" done={storageReady}>
          <p>Data folder: {onboarding.storage.dataDir} ({gigabytes(onboarding.storage.freeBytes)}).</p>
          <p>{storageReady ? 'The database is ready.' : 'The database is not ready; restart PHOENIX.'} To use another drive, set PHOENIX_DATA_DIR before starting PHOENIX.</p>
        </Step>
        <Step number={4} title="Choose your document folder" done={workspaceReady}>
          <p>PHOENIX may read documents in this folder during planned missions, and only with your permission.</p>
          <WorkspaceForm connection={connection} store={store} id={firstRun ? 'first-run-workspace' : 'onboarding-workspace'} />
        </Step>
      </ol>
      <div className="setup-actions">
        <button type="button" disabled={connection.checking} onClick={() => void store.recheck()}>{connection.checking ? 'Checking…' : 'Re-check'}</button>
        {firstRun && <button type="button" onClick={() => store.dismissOnboarding()}>{onboarding.complete ? 'Continue to PHOENIX' : 'Continue without finishing'}</button>}
      </div>
      {firstRun && !onboarding.complete && <p className="setup-feedback">Text chat works without a document folder. Missions fail explicitly until the runtime and required models are ready. Onboarding stays available from the navigation.</p>}
    </section>
  );
}

export function FirstRunScreen({ connection, store }: { connection: ConnectionView; store: SetupStore }) {
  return (
    <main id="main-content" className="first-run" aria-label="First-run setup">
      <OnboardingPanel connection={connection} store={store} firstRun />
    </main>
  );
}

export function SettingsPanel({ connection, store }: { connection: ConnectionView; store: SetupStore }) {
  const settings = connection.settings;
  const [choice, setChoice] = useState<MissionPath | undefined>(undefined);
  if (connection.backend.state !== 'ready' || !settings) {
    return (
      <section className="setup-panel" aria-labelledby="settings-heading">
        <h3 id="settings-heading">Settings</h3>
        <p>Settings are unavailable until the local backend is connected.</p>
      </section>
    );
  }
  const selected = choice ?? settings.defaultMissionPath;
  const voice = connection.capabilities?.find((capability) => capability.capabilityId === 'voice');
  const memory = connection.capabilities?.find((capability) => capability.capabilityId === 'memory');
  return (
    <section className="setup-panel" aria-labelledby="settings-heading">
      <h3 id="settings-heading">Settings</h3>
      <div className="settings-sections">
        <section aria-labelledby="settings-workspace"><h4 id="settings-workspace">Document workspace</h4>
          <WorkspaceForm connection={connection} store={store} id="settings-workspace-root" />
        </section>
        <section aria-labelledby="settings-path"><h4 id="settings-path">Default mission path</h4>
          <form className="setup-form" onSubmit={(event) => { event.preventDefault(); void store.setDefaultPath(selected); }}>
            <p>Saved: {missionPathText[settings.defaultMissionPath]}. New missions start with it; the bar above can override it for this launch.</p>
            <label htmlFor="default-mission-path">Default for new missions</label>
            <select id="default-mission-path" value={selected} onChange={(event) => setChoice(event.currentTarget.value as MissionPath)}>
              <option value="auto">{missionPathText.auto}</option>
              <option value="direct">{missionPathText.direct}</option>
              <option value="complex">{missionPathText.complex}</option>
            </select>
            <button type="submit">Save default</button>
            <p className="setup-feedback" role="status" aria-live="polite">{connection.settingsMessage}</p>
          </form>
        </section>
        <section aria-labelledby="settings-models"><h4 id="settings-models">Models</h4>
          <p>Model tiers are fixed in this version (read-only); model selection arrives in a later ticket.</p>
          <ul className="setup-list">
            {settings.modelTiers.map((tier) => <li key={tier.role}>{tier.role}: {tier.models.join(' → ') || 'none'}</li>)}
            <li>default: {settings.defaultTier.join(' → ') || 'none'}</li>
          </ul>
        </section>
        <section aria-labelledby="settings-voice"><h4 id="settings-voice">Voice</h4>
          <p>{capabilityLine(voice, 'Voice')} Text chat stays fully available.</p>
        </section>
        <section aria-labelledby="settings-memory"><h4 id="settings-memory">Memory and retention</h4>
          <p>{capabilityLine(memory, 'Long-term memory')} Mission history is kept for {settings.retentionDays ?? 'an unreported number of'} days.</p>
        </section>
        <section aria-labelledby="settings-permissions"><h4 id="settings-permissions">Privacy and permissions</h4>
          <p>PermissionEngine alone authorizes actions. Approve or deny requests in Permissions; nothing here changes a permission.</p>
        </section>
      </div>
    </section>
  );
}

function capabilityLine(capability: CapabilityView | undefined, label: string): string {
  if (!capability) return `${label}: status unavailable.`;
  return capability.enabled ? `${label} is enabled in this build.` : `${label} is not yet enabled in this build (arrives with ${capability.ticket}).`;
}

const capabilityViews = {
  computer: { title: 'Computer Control', ids: ['computer_control'], what: 'operate desktop apps through accessibility, vision and, last, mouse and keyboard' },
  research: { title: 'Research', ids: ['web_research'], what: 'search the web and answer with cited, provenance-checked sources' },
  media: { title: 'Media', ids: ['media', 'document_generation'], what: 'control your authorized Spotify and YouTube connections and generate PDF, DOCX and Markdown documents' },
} as const;
const capabilityNames: Readonly<Record<string, string>> = {
  computer_control: 'Computer control', web_research: 'Web research', media: 'Spotify and YouTube', document_generation: 'Document generation',
};

/** Computer Control, Research and Media: driven by the backend capability flags (ADR-112). */
export function CapabilityPanel({ view, connection }: { view: keyof typeof capabilityViews; connection: ConnectionView }) {
  const spec = capabilityViews[view];
  const flags = spec.ids.map((id) => ({ id, flag: connection.capabilities?.find((capability) => capability.capabilityId === id) }));
  const known = connection.backend.state === 'ready' && connection.capabilities !== undefined;
  return (
    <section className="setup-panel" aria-labelledby={`${view}-capability-heading`}>
      <h3 id={`${view}-capability-heading`}>{spec.title}</h3>
      <p>When enabled, PHOENIX can {spec.what} inside your missions.</p>
      {!known ? <p role="status">Capability status is unavailable until the local backend is connected.</p> :
        <ul className="setup-list">
          {flags.map(({ id, flag }) => {
            const agent = agentRoster.find((item) => item.internalId === flag?.agentId);
            const owner = agent ? ` Carried by ${agent.alias} (${agent.professionalRole}).` : '';
            return <li key={id}>
              <strong>{capabilityNames[id]}: {flag ? (flag.enabled ? 'enabled' : 'not yet enabled') : 'status unavailable'}</strong>
              {flag && !flag.enabled && <> — arrives with {flag.ticket}. PHOENIX does not attempt it in missions until then.</>}
              {flag?.enabled && <> — ask PHOENIX in Home / Chat; steps appear in Task Graph and each action asks in Permissions.</>}
              {owner}
            </li>;
          })}
        </ul>}
      <p>Every action runs inside a mission through ToolRegistry and PermissionEngine; risky actions wait for your approval in Permissions.</p>
    </section>
  );
}
