// PHX UI (ADR-310): the site-builder contract (CONTRACTS_2026-11-05) as one typed API with two
// implementations. `createSitesApi()` is the single switch: the native shell gets the real one
// (allowlisted proxy routes), a plain browser or VITE_PHOENIX_MOCK=1 gets the in-memory mock.

import { createTauriTransport, nativeShellAvailable } from './tauri';
import type { Transport } from './core';

export type SiteKind = 'landing' | 'portfolio' | 'business';
export type SectionKind = 'hero' | 'features' | 'gallery' | 'testimonials' | 'pricing' | 'contact' | 'footer';
export type Section = { id: string; kind: SectionKind; layout: 'a' | 'b' | 'c'; title: string; body: string; items?: { title: string; body: string }[]; image: string | null };
export type SiteSpec = { id: string; name: string; type: SiteKind; theme: { primary: string; bg: string; text: string; font: 'serif' | 'sans' | 'mono' | 'display' }; sections: Section[] };
export type Phase = 'planning' | 'spec' | 'render' | 'ready';
export const PHASES: readonly Phase[] = ['planning', 'spec', 'render', 'ready'];
export type SiteSummary = { id: string; name: string; type: SiteKind; head: number; max: number; updated_at: string };
export type SiteState = { id: string; name: string; type: SiteKind; status: 'building' | 'ready' | 'failed'; phase: Phase; head: number; max: number; spec?: SiteSpec; error?: string; message?: string };
export type CommandOutcome = { kind: 'site' | 'mission'; site_id?: string; mission_id?: string; message: string };
export type TrustState = { enabled: boolean; since: string | null; allow: string[]; deny: string[] };

export interface SitesApi {
  readonly kind: 'mock' | 'real';
  list(): Promise<SiteSummary[]>;
  get(id: string): Promise<SiteState>;
  command(text: string, activeSiteId?: string): Promise<CommandOutcome>;
  edit(id: string, instruction: string, sectionId?: string): Promise<SiteState>;
  undo(id: string): Promise<SiteState>;
  redo(id: string): Promise<SiteState>;
  trust(): Promise<TrustState>;
  setTrust(enabled: boolean): Promise<TrustState>;
  voice(): Promise<string | undefined>;
}

// ---- rendering (client side: the srcdoc inherits the shell CSP, so style lives in a CSSOM sheet) ----
const esc = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] as string);
const FONTS = { serif: 'Georgia, "Times New Roman", serif', sans: '"Segoe UI", system-ui, sans-serif', mono: 'Consolas, "Courier New", monospace', display: 'Impact, "Arial Narrow", sans-serif' };
const HEX = /^#[0-9a-fA-F]{6}$/;
const hex = (value: string, fallback: string) => (HEX.test(value) ? value : fallback);

export function renderSite(spec: SiteSpec): { css: string; body: string } {
  const { primary, bg, text, font } = spec.theme;
  const css = `*{box-sizing:border-box}body{margin:0;background:${hex(bg, '#111111')};color:${hex(text, '#f4f4f4')};font-family:${FONTS[font] ?? FONTS.sans};line-height:1.55}
section{padding:72px 8vw;cursor:pointer;position:relative;outline:2px solid transparent;outline-offset:-6px}
section:hover{outline-color:${hex(primary, '#ff6a2b')}66}section.phx-selected{outline:3px solid ${hex(primary, '#ff6a2b')}}
h1,h2{margin:0 0 16px;line-height:1.05}h1{font-size:clamp(36px,6vw,72px)}h2{font-size:clamp(26px,3.4vw,42px)}p{margin:0 0 12px;max-width:62ch;opacity:.86}
.hero{min-height:56vh;display:flex;flex-direction:column;justify-content:center}.hero.b{align-items:center;text-align:center}.hero.c{background:${hex(primary, '#ff6a2b')};color:#0a0a0a}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:20px;margin-top:24px}.grid>div{padding:20px;background:${hex(primary, '#ff6a2b')}1f;border-left:4px solid ${hex(primary, '#ff6a2b')}}
.grid h3{margin:0 0 6px}.b .grid{grid-template-columns:1fr}.c .grid>div{border:0;border-top:4px solid ${hex(primary, '#ff6a2b')}}
.img{aspect-ratio:16/9;background:${hex(primary, '#ff6a2b')}33;display:grid;place-items:center;font-size:12px;margin-top:16px}
a.btn,.btn{display:inline-block;margin-top:12px;padding:12px 22px;background:${hex(primary, '#ff6a2b')};color:#0a0a0a;font-weight:700;text-decoration:none}
footer{padding:36px 8vw;opacity:.7;cursor:pointer;font-size:14px}footer.phx-selected{outline:3px solid ${hex(primary, '#ff6a2b')}}`;
  const body = spec.sections.map((s) => {
    const items = (s.items ?? []).map((i) => `<div><h3>${esc(i.title)}</h3><p>${esc(i.body)}</p></div>`).join('');
    const img = s.image ? `<div class="img">${esc(s.image)}</div>` : '';
    if (s.kind === 'footer') return `<footer data-section-id="${esc(s.id)}">${esc(s.title)} ${esc(s.body)}</footer>`;
    const head = s.kind === 'hero' ? `<h1>${esc(s.title)}</h1>` : `<h2>${esc(s.title)}</h2>`;
    const cta = s.kind === 'hero' || s.kind === 'contact' ? '<span class="btn">Get in touch</span>' : '';
    return `<section class="${s.kind} ${s.layout}" data-section-id="${esc(s.id)}">${head}<p>${esc(s.body)}</p>${items ? `<div class="grid">${items}</div>` : ''}${img}${cta}</section>`;
  }).join('');
  return { css, body };
}

// ---- mock (in-memory; mirrors the contract incl. phases, versions, undo/redo) ----
const KEYWORDS: [RegExp, Partial<SiteSpec['theme']>][] = [
  [/bak|cafe|coffee|food|restaurant/i, { primary: '#d98a3d', bg: '#1a120b', text: '#f6ead9', font: 'serif' }],
  [/photo|art|portfolio|design/i, { primary: '#e8e8e8', bg: '#0c0c0c', text: '#f2f2f2', font: 'display' }],
  [/tech|app|saas|code|dev/i, { primary: '#2ee6ff', bg: '#071014', text: '#e6f7fb', font: 'mono' }],
];
const COLORS: Record<string, string> = { red: '#e5372b', orange: '#ff6a2b', blue: '#2f7bff', green: '#2ea36b', purple: '#8a5cf0', cyan: '#2ee6ff', yellow: '#f2c230', pink: '#e8509b', white: '#f2f2f2', black: '#0a0a0a' };
const slug = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'site';

function mockSpec(id: string, prompt: string): SiteSpec {
  const theme = { primary: '#ff6a2b', bg: '#0d0d10', text: '#f4f4f4', font: 'sans' as const, ...(KEYWORDS.find(([re]) => re.test(prompt))?.[1] ?? {}) };
  const name = (prompt.match(/(?:for|called|named)\s+(?:a |an |the |my )?([a-z0-9' -]{2,30})/i)?.[1] ?? 'New project').trim().replace(/\b\w/g, (c) => c.toUpperCase());
  const item = (t: string, b: string) => ({ title: t, body: b });
  return {
    id, name, type: /portfolio/i.test(prompt) ? 'portfolio' : /business|shop|store/i.test(prompt) ? 'business' : 'landing', theme,
    sections: [
      { id: 's1', kind: 'hero', layout: 'a', title: name, body: 'A fresh start, built in seconds on your own machine.', image: null },
      { id: 's2', kind: 'features', layout: 'a', title: 'What we do', body: 'Three things we care about.', items: [item('Craft', 'Made with care.'), item('Speed', 'Ready when you are.'), item('Trust', 'Local and private.')], image: null },
      { id: 's3', kind: 'testimonials', layout: 'b', title: 'Kind words', body: 'Placeholder feedback to replace.', items: [item('A. Customer', 'Exactly what I needed.')], image: null },
      { id: 's4', kind: 'contact', layout: 'c', title: 'Say hello', body: 'Tell us about your project.', image: null },
      { id: 's5', kind: 'footer', layout: 'a', title: name, body: 'All rights reserved.', image: null },
    ],
  };
}

type MockSite = { spec: SiteSpec; versions: SiteSpec[]; head: number; phase: Phase; status: SiteState['status']; updated: string; t0: number };

export function createMockSitesApi(): SitesApi {
  const sites = new Map<string, MockSite>();
  let trust: TrustState = { enabled: false, since: null, allow: [], deny: ['system folders', 'secrets', 'browser profiles'] };
  const phaseOf = (s: MockSite): Phase => { const age = Date.now() - s.t0; return age < 1200 ? 'planning' : age < 2600 ? 'spec' : age < 3600 ? 'render' : 'ready'; };
  const state = (s: MockSite, message?: string): SiteState => {
    const phase = phaseOf(s);
    return { id: s.spec.id, name: s.spec.name, type: s.spec.type, status: phase === 'ready' ? 'ready' : 'building', phase, head: s.head, max: s.versions.length, spec: phase === 'ready' ? s.versions[s.head - 1] : undefined, message };
  };
  const need = (id: string) => { const s = sites.get(id); if (!s) throw new Error('no such site'); return s; };
  const push = (s: MockSite, spec: SiteSpec) => { s.versions = [...s.versions.slice(0, s.head), spec]; s.head = s.versions.length; s.updated = new Date().toISOString(); s.t0 = Date.now() - 2600; };
  const create = (prompt: string) => {
    let id = slug(mockSpec('x', prompt).name), n = 1;
    while (sites.has(id)) id = `${slug(mockSpec('x', prompt).name)}-${++n}`;
    const spec = mockSpec(id, prompt);
    sites.set(id, { spec, versions: [spec], head: 1, phase: 'planning', status: 'building', updated: new Date().toISOString(), t0: Date.now() });
    return id;
  };
  const edit = (id: string, instruction: string, sectionId?: string): SiteState => {
    const s = need(id), cur = structuredClone(s.versions[s.head - 1]!);
    const color = Object.keys(COLORS).find((c) => new RegExp(`\\b${c}\\b`, 'i').test(instruction));
    const font = (['serif', 'sans', 'mono', 'display'] as const).find((f) => new RegExp(`\\b${f}\\b`, 'i').test(instruction));
    const quoted = instruction.match(/(?:to|say|says|reads?)\s+"?([^"]+?)"?\s*$/i)?.[1];
    const target = cur.sections.find((x) => x.id === sectionId);
    if (/remove|delete/i.test(instruction) && target && target.kind !== 'hero' && target.kind !== 'footer' && cur.sections.length > 3) cur.sections = cur.sections.filter((x) => x !== target);
    else if (color) cur.theme.primary = COLORS[color] ?? cur.theme.primary;
    else if (font) cur.theme.font = font;
    else if (target && quoted) target[/body|text|paragraph/i.test(instruction) ? 'body' : 'title'] = quoted.slice(0, 200);
    else return state(s, 'Edit rejected: say which section and what text, a colour, or a font.');
    push(s, cur);
    return state(s, 'Edited.');
  };
  const step = (id: string, d: 1 | -1) => {
    const s = need(id), next = s.head + d;
    if (next < 1 || next > s.versions.length) return state(s, d < 0 ? 'Nothing to undo.' : 'Nothing to redo.');
    s.head = next; s.updated = new Date().toISOString();
    return state(s);
  };
  return {
    kind: 'mock',
    async list() { return [...sites.values()].map((s) => ({ id: s.spec.id, name: s.spec.name, type: s.spec.type, head: s.head, max: s.versions.length, updated_at: s.updated })); },
    async get(id) { return state(need(id)); },
    async command(text, active) {
      const t = text.trim();
      if (/^(design|build|make|create)\b.*\b(website|site|landing|portfolio)\b/i.test(t)) return { kind: 'site', site_id: create(t), message: 'Building your site.' };
      const open = t.match(/^open (?:my )?(.+?)(?: site)?$/i);
      const found = open && [...sites.values()].find((s) => s.spec.name.toLowerCase().includes((open[1] ?? "").toLowerCase()));
      if (found) return { kind: 'site', site_id: found.spec.id, message: `Opened ${found.spec.name}.` };
      if (active && /^undo\b/i.test(t)) return { kind: 'site', site_id: active, message: step(active, -1).message ?? 'Undone.' };
      if (active && /^redo\b/i.test(t)) return { kind: 'site', site_id: active, message: step(active, 1).message ?? 'Redone.' };
      if (active && (/^(change|set|make|add|remove|delete|rename|use|swap)\b/i.test(t) || /\b(section|title|color|colour|font|theme)\b/i.test(t))) return { kind: 'site', site_id: active, message: edit(active, t).message ?? 'Edited.' };
      return { kind: 'mission', mission_id: crypto.randomUUID(), message: '[mock] This would go to the mission path.' };
    },
    async edit(id, instruction, sectionId) { return edit(id, instruction, sectionId); },
    async undo(id) { return step(id, -1); },
    async redo(id) { return step(id, 1); },
    async trust() { return trust; },
    async setTrust(enabled) { trust = { ...trust, enabled, since: enabled ? new Date().toISOString() : null, allow: enabled ? ['P1', 'P2', 'P3 (trust_mode)'] : [] }; return trust; },
    async voice() { return undefined; },
  };
}

// ---- real (proxy routes; ids are slugs) ----
function real(transport: Transport): SitesApi {
  const call = async <T>(method: 'GET' | 'POST', path: string, body?: Record<string, unknown>): Promise<T> => {
    const result = await transport.request(method, path, body);
    if (result.status >= 400 || result.body === null || typeof result.body !== 'object') {
      const detail = (result.body as { detail?: unknown } | null)?.detail;
      throw new Error(typeof detail === 'string' ? detail : `request failed (${result.status})`);
    }
    return result.body as T;
  };
  const id = (value: string) => encodeURIComponent(value);
  return {
    kind: 'real',
    list: () => call('GET', '/sites'),
    get: (s) => call('GET', `/sites/${id(s)}`),
    command: (text, active) => call('POST', '/command', { text, ...(active ? { active_site_id: active } : {}) }),
    edit: (s, instruction, sectionId) => call('POST', `/sites/${id(s)}/edit`, { instruction, ...(sectionId ? { section_id: sectionId } : {}) }),
    undo: (s) => call('POST', `/sites/${id(s)}/undo`),
    redo: (s) => call('POST', `/sites/${id(s)}/redo`),
    trust: () => call('GET', '/trust'),
    setTrust: (enabled) => call('POST', '/trust', { enabled }),
    voice: async () => { try { return (await call<{ state?: string }>('GET', '/voice/status')).state; } catch { return undefined; } },
  };
}

/** The one switch: mock in a plain browser (or VITE_PHOENIX_MOCK=1), real inside the native shell. */
export function createSitesApi(): SitesApi {
  const forceMock = import.meta.env.VITE_PHOENIX_MOCK === '1';
  return nativeShellAvailable() && !forceMock ? real(createTauriTransport()) : createMockSitesApi();
}
