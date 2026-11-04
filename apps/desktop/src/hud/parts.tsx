import { useEffect, useRef, type ReactNode } from 'react';
import { PHASES, renderSite, type Phase, type SiteSpec } from '../adapters/sites';

export type OrbState = 'idle' | 'listening' | 'thinking' | 'speaking';

/** The PHOENIX orb: pure CSS/SVG, four states (data-state), docks to the corner when `docked`. */
export function Orb({ state, docked }: { state: OrbState; docked: boolean }) {
  return (
    <div className="orb" data-state={state} data-docked={docked} role="img" aria-label={`PHOENIX is ${state}`}>
      <span className="orb-halo" />
      <svg viewBox="-60 -60 120 120" aria-hidden="true">
        <circle className="orb-ring r1" r="52" />
        <circle className="orb-ring r2" r="42" />
        <circle className="orb-ring r3" r="32" />
        <circle className="orb-core" r="20" />
      </svg>
      <span className="orb-state">{state}</span>
    </div>
  );
}

/** Outlined HUD frame: four corner brackets, a mono uppercase label and an optional state chip. */
export function Frame({ label, state, tone, className = '', children }: { label: string; state?: string; tone?: 'live' | 'warn' | 'done'; className?: string; children?: ReactNode }) {
  return (
    <section className={`frame ${className}`} data-tone={tone}>
      <i className="fc tl" /><i className="fc tr" /><i className="fc bl" /><i className="fc br" />
      <header className="frame-head"><span>{label}</span>{state && <b>{state}</b>}</header>
      {children}
    </section>
  );
}

const PHASE_TEXT: Record<Phase, string> = { planning: 'Reading your request', spec: 'Drafting the structure', render: 'Rendering the page', ready: 'Ready' };

/** Progress frames driven by GET /v1/sites/{id} `phase`. */
export function Progress({ phase, failed }: { phase: Phase; failed?: string }) {
  const at = PHASES.indexOf(phase);
  return (
    <div className="progress" aria-live="polite">
      {PHASES.map((p, i) => (
        <Frame key={p} label={p} tone={failed && i === at ? 'warn' : i < at ? 'done' : i === at ? 'live' : undefined} state={failed && i === at ? 'failed' : i < at ? 'done' : i === at ? 'running' : 'waiting'}>
          <p>{failed && i === at ? failed : PHASE_TEXT[p]}</p>
        </Frame>
      ))}
    </div>
  );
}

/**
 * Live preview. The srcdoc inherits the shell CSP (no inline script/style), so it is sandboxed without
 * scripts: the parent injects the stylesheet through the CSSOM and listens for clicks itself.
 */
export function Preview({ spec, selected, onSelect }: { spec: SiteSpec; selected?: string; onSelect(id: string | undefined): void }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const { css, body } = renderSite(spec);
  const mark = () => {
    const doc = frame.current?.contentDocument;
    doc?.querySelectorAll('[data-section-id]').forEach((el) => el.classList.toggle('phx-selected', el.getAttribute('data-section-id') === selected));
  };
  const load = () => {
    const win = frame.current?.contentWindow as (Window & { CSSStyleSheet: typeof CSSStyleSheet }) | null | undefined;
    const doc = frame.current?.contentDocument;
    if (!win || !doc) return;
    const sheet = new win.CSSStyleSheet();
    sheet.replaceSync(css);
    doc.adoptedStyleSheets = [sheet];
    doc.addEventListener('click', (event) => {
      event.preventDefault();
      onSelect((event.target as Element).closest('[data-section-id]')?.getAttribute('data-section-id') ?? undefined);
    });
    mark();
  };
  useEffect(mark, [selected]);
  return <iframe ref={frame} className="preview" title={`Live preview of ${spec.name}`} sandbox="allow-same-origin" srcDoc={`<!doctype html><meta charset="utf-8"><body>${body}</body>`} onLoad={load} />;
}
