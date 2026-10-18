import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { ConnectedApp } from './adapters/ConnectedApp';
import { PhoenixStore } from './adapters/core';
import { createTauriTransport, nativeShellAvailable } from './adapters/tauri';
import './styles.css';

const root = document.getElementById('root');
if (!root) {
  throw new Error('Phoenix desktop mount element is missing.');
}

// PHX-107 (ADR-092): inside the native shell, every port is backed by the PHX-106 proxy.
// A plain browser (vite dev without Tauri) has no backend path and keeps the honest
// unconnected App.
if (nativeShellAvailable()) {
  const store = new PhoenixStore(createTauriTransport(), {
    setTimeout: (callback, ms) => window.setTimeout(callback, ms),
    clearTimeout: (handle) => window.clearTimeout(handle as number),
    setInterval: (callback, ms) => window.setInterval(callback, ms),
    clearInterval: (handle) => window.clearInterval(handle as number),
  });
  void store.start();
  createRoot(root).render(
    <StrictMode>
      <ConnectedApp store={store} />
    </StrictMode>,
  );
} else {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
