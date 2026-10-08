import { useEffect, useRef, useState } from 'react';

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

export function ViewPanel({ view }: { view: ViewDefinition }) {
  if (view.id === 'home') {
    return (
      <>
        <section className="welcome-panel" aria-labelledby="welcome-heading">
          <p className="eyebrow">A place to begin</p>
          <h3 id="welcome-heading">Make room for the next mission.</h3>
          <p>One PHOENIX identity. A clear space for your conversations, context and work.</p>
          <p className="welcome-note">Explore the workspaces using navigation. Conversation and live workspace connections are coming next.</p>
        </section>
        <section className="workspace-overview" aria-labelledby="overview-heading">
          <div className="section-heading">
            <h3 id="overview-heading">Your workspace, taking shape</h3>
            <p>A preview of the areas ahead</p>
          </div>
          <div className="overview-grid">
            <article className="overview-card">
              <span className="card-index" aria-hidden="true">01</span>
              <h4>Keep the mission clear</h4>
              <p>Conversation, specialist roles and task plans will share one place to work.</p>
            </article>
            <article className="overview-card">
              <span className="card-index" aria-hidden="true">02</span>
              <h4>Give context a home</h4>
              <p>Memory, documents and research will keep useful information within reach.</p>
            </article>
            <article className="overview-card">
              <span className="card-index" aria-hidden="true">03</span>
              <h4>Stay in control</h4>
              <p>Permissions and configuration will make scope and choices visible to you.</p>
            </article>
          </div>
        </section>
        <aside className="connection-note" aria-label="Workspace availability">
          <strong>Navigation is ready. Workspace connections are pending.</strong>
          <p>Nothing here submits a conversation or performs an action. You can explore without enabling a microphone.</p>
        </aside>
      </>
    );
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

export default function App() {
  const [selectedView, setSelectedView] = useState<ViewId>('home');
  const mainRef = useRef<HTMLElement>(null);
  const focusRequested = useRef(false);
  const currentView = resolveView(selectedView);

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
        <div className="page-content"><ViewPanel view={currentView} /></div>
      </main>
    </div>
  );
}
