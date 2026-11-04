// PHX UI (ADR-310 to ADR-314): the PHOENIX HUD. No chat thread: an orb, one frame per agent, a live
// site preview, a command bar and the TRUST badge. The previous workspace (setup, settings, voice,
// memory, files, permissions, diagnostics) stays reachable under the SYSTEM tab as LegacyApp.

import { useCallback, useEffect, useMemo, useState, type ComponentProps, type FormEvent } from 'react';
import { LegacyApp, agentRoster, readAgentSnapshot, readObservabilityPort } from './App';
import { createSitesApi, type SiteSpec, type SiteState, type SiteSummary, type TrustState } from './adapters/sites';
import { Frame, Orb, Preview, Progress, type OrbState } from './hud/parts';
import './hud/hud.css';

type Tab = 'agents' | 'sites' | 'activity' | 'system';
const TABS: readonly Tab[] = ['agents', 'sites', 'activity', 'system'];
const stamp = () => new Date().toLocaleTimeString([], { hour12: false });

export default function Hud(props: ComponentProps<typeof LegacyApp> = {}) {
  const api = useMemo(createSitesApi, []);
  const [tab, setTab] = useState<Tab>('agents');
  const [siteId, setSiteId] = useState<string>();
  const [site, setSite] = useState<SiteState>();
  const [spec, setSpec] = useState<SiteSpec>();
  const [sites, setSites] = useState<SiteSummary[]>([]);
  const [selected, setSelected] = useState<string>();
  const [trust, setTrust] = useState<TrustState>();
  const [armed, setArmed] = useState(false);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [log, setLog] = useState<{ at: string; line: string }[]>([]);
  const [mic, setMic] = useState(false);
  const [voice, setVoice] = useState<string>();

  const note = useCallback((line: string) => { setMessage(line); setLog((l) => [{ at: stamp(), line }, ...l].slice(0, 60)); }, []);
  const adopt = useCallback((next: SiteState) => { setSite(next); if (next.spec) setSpec(next.spec); }, []);
  const building = site?.status === 'building';

  // Progress frames follow GET /sites/{id}: poll while building (or until the first state arrives).
  useEffect(() => {
    if (!siteId || (site && site.id === siteId && !building)) return;
    const timer = window.setTimeout(() => { api.get(siteId).then(adopt, (e: Error) => note(`Site unavailable: ${e.message}`)); }, 700);
    return () => window.clearTimeout(timer);
  }, [api, siteId, site, building, adopt, note]);
  useEffect(() => { if (site?.status === 'ready') note(`${site.name} ready (v${site.head}/${site.max}).`); }, [site?.id, site?.status, note]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { void api.list().then(setSites, () => undefined); }, [api, tab, site?.head, site?.max, site?.status]);
  useEffect(() => {
    const load = () => { void api.trust().then(setTrust, () => undefined); void api.voice().then(setVoice); };
    load();
    const timer = window.setInterval(load, 4000);
    return () => window.clearInterval(timer);
  }, [api]);

  const orb: OrbState = mic || voice === 'listening' || props.voicePort?.state === 'listening' ? 'listening'
    : busy || building || voice === 'thinking' || props.voicePort?.state === 'thinking' ? 'thinking'
    : voice === 'speaking' || props.voicePort?.state === 'speaking' ? 'speaking' : 'idle';

  async function toggleTrust(enabled: boolean) {
    setArmed(false);
    try { setTrust(await api.setTrust(enabled)); note(enabled ? 'Trust mode ON.' : 'Trust mode OFF (kill switch).'); } catch (e) { note(`Trust change failed: ${(e as Error).message}`); }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const line = text.trim();
    if (!line || busy) return;
    setText('');
    const trustPhrase = line.match(/^trust mode (on|off)\.?$/i);
    if (trustPhrase) { void toggleTrust(trustPhrase[1]?.toLowerCase() === 'on'); return; }
    setBusy(true);
    note(`> ${line}`);
    try {
      if (selected && siteId) {
        adopt(await api.edit(siteId, line, selected));
        setSelected(undefined);
      } else {
        const out = await api.command(line, siteId);
        note(out.message);
        if (out.kind === 'site' && out.site_id) { if (out.site_id !== siteId) { setSite(undefined); setSpec(undefined); } setSiteId(out.site_id); setSelected(undefined); }
        else setTab('agents');
      }
    } catch (e) { note(`Command failed: ${(e as Error).message}`); } finally { setBusy(false); }
  }

  async function step(kind: 'undo' | 'redo') {
    if (!siteId) return;
    try { const next = await api[kind](siteId); adopt(next); if (next.message) note(next.message); } catch (e) { note(`${kind} failed: ${(e as Error).message}`); }
  }

  const agents = readAgentSnapshot(props.agentPort);
  const simId = site?.phase === 'planning' ? 'strategist' : site?.phase === 'spec' ? 'build_engineer' : 'verifier';
  const simulated = api.kind === 'mock' && building ? { strategist: 'planning', build_engineer: 'building', verifier: 'checking' }[simId] : undefined;
  const roster = agentRoster.map((a) => {
    const live = agents.agents.find((s) => s.internalId === a.internalId);
    const state = live?.state ?? (simulated && a.internalId === simId ? 'working' : 'idle');
    return { ...a, state, task: live?.currentTask ?? (state === 'working' ? simulated : undefined) };
  }).sort((a, b) => Number(b.state !== 'idle') - Number(a.state !== 'idle'));
  const logs = readObservabilityPort(props.observabilityPort).logs.slice(0, 30);
  const sectionOf = spec?.sections.find((s) => s.id === selected);
  const content = Boolean(spec || siteId) || tab === 'system';

  return (
    <div className="hud" data-content={content}>
      <Orb state={orb} docked={content} />
      <header className="hud-top">
        <h1 className="wordmark">PHOENIX</h1>
        <nav className="tabs" aria-label="PHOENIX sections">
          {TABS.map((t) => <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>{t}</button>)}
        </nav>
        <div className="trust" data-on={trust?.enabled ?? false}>
          {armed ? <>
            <span>Enable trust mode?</span>
            <button type="button" onClick={() => void toggleTrust(true)}>Confirm</button>
            <button type="button" onClick={() => setArmed(false)}>Cancel</button>
          </> : <button type="button" disabled={!trust} aria-pressed={trust?.enabled ?? false} onClick={() => (trust?.enabled ? void toggleTrust(false) : setArmed(true))}
            title={trust?.enabled ? 'Kill switch: click to turn trust mode off now' : 'Trust mode is off. Click to enable.'}>
            {trust?.enabled ? 'TRUST MODE ON · KILL' : 'TRUST MODE OFF'}
          </button>}
        </div>
      </header>

      {tab === 'system' ? (
        <div className="hud-system"><LegacyApp {...props} /></div>
      ) : (
        <div className="hud-body">
          <aside className="rail" aria-label={tab}>
            {tab === 'agents' && <div className="agent-grid">{roster.map((a) => (
              <Frame key={a.internalId} label={a.alias} state={a.state.replace('_', ' ')} tone={a.state === 'working' ? 'live' : a.state === 'idle' ? undefined : 'warn'}>
                <p>{a.task ?? a.professionalRole}</p>
              </Frame>
            ))}</div>}
            {tab === 'sites' && (
              <Frame label="Projects" state={`${sites.length}`}>
                {sites.length === 0 && <p>No sites yet. Try: design a landing page for a bakery.</p>}
                <ul className="site-list">{sites.map((s) => (
                  <li key={s.id}><button type="button" aria-current={s.id === siteId} onClick={() => { if (s.id !== siteId) { setSiteId(s.id); setSite(undefined); setSpec(undefined); setSelected(undefined); } }}>
                    <span>{s.name}</span><small>{s.type} · v{s.head}/{s.max}</small></button></li>
                ))}</ul>
                {siteId && site && <div className="versions">
                  <header className="frame-head"><span>Versions</span><b>v{site.head}/{site.max}</b></header>
                  <div className="chips">{Array.from({ length: site.max }, (_, i) => <span key={i} data-head={i + 1 === site.head} data-redo={i + 1 > site.head}>v{i + 1}</span>)}</div>
                  <div className="actions">
                    <button type="button" disabled={site.head <= 1 || building} onClick={() => void step('undo')}>Undo</button>
                    <button type="button" disabled={site.head >= site.max || building} onClick={() => void step('redo')}>Redo</button>
                  </div>
                </div>}
              </Frame>
            )}
            {tab === 'activity' && (
              <Frame label="Activity" state={`${log.length + logs.length}`}>
                <ol className="activity">
                  {log.map((l, i) => <li key={`l${i}`}><time>{l.at}</time>{l.line}</li>)}
                  {logs.map((l) => <li key={l.id}><time>{l.severity}</time>{l.subsystem}: {l.message}</li>)}
                  {log.length + logs.length === 0 && <li>Nothing yet.</li>}
                </ol>
              </Frame>
            )}
          </aside>

          <main className="stage" id="main-content">
            {spec ? (
              <Frame className="preview-frame" label={`${spec.name} · ${building ? 'rebuilding' : 'live preview'}`} state={site ? `v${site.head}/${site.max}` : undefined} tone={building ? 'live' : 'done'}>
                <Preview spec={spec} selected={selected} onSelect={setSelected} />
                {building && site && <div className="scrim"><Progress phase={site.phase} failed={site.error} /></div>}
              </Frame>
            ) : siteId ? (
              <Progress phase={site?.phase ?? 'planning'} failed={site?.error} />
            ) : (
              <p className="hint">Tell PHOENIX what to build, or ask it anything.<br /><small>design a landing page for a bakery</small></p>
            )}
          </main>
        </div>
      )}

      <form className="command" onSubmit={(e) => void submit(e)}>
        {sectionOf && <button type="button" className="chip" onClick={() => setSelected(undefined)} title="Clear selection">
          Section {sectionOf.id} · {sectionOf.kind} ×</button>}
        <input aria-label="Command" value={text} onChange={(e) => setText(e.target.value)} disabled={busy}
          placeholder={sectionOf ? `Change the ${sectionOf.kind} section…` : spec ? 'Edit the site, undo, redo, or give a new command…' : 'Command PHOENIX…'} />
        {props.panels?.above?.home
          ? <details className="voice"><summary>Voice</summary>{props.panels.above.home}</details>
          : <button type="button" aria-pressed={mic} onClick={() => setMic((m) => !m)}>{mic ? 'Mic on (mock)' : 'Mic'}</button>}
        <button type="submit" disabled={busy || !text.trim()}>Send</button>
        <output className="reply" aria-live="polite">{message}</output>
      </form>
    </div>
  );
}
