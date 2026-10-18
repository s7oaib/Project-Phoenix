// PHX-107 (ADR-092): the webview side of the PHX-106 native proxy.
//
// Exactly two commands (`api_request`, `backend_status`) and three shell events. The Rust
// shell owns the backend address and credential and adds it itself; this file only names an
// HTTP method, a contract path and an optional JSON body. Nothing here can reach a model
// provider, files or the OS: the capability grants no plugin, and connect-src allows only IPC.

import { invoke, isTauri } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import type { ApiResult, HttpMethod, Transport, TransportHandlers } from './core';

export const COMMANDS = Object.freeze({ request: 'api_request', status: 'backend_status' } as const);
export const EVENTS = Object.freeze({ event: 'phoenix:event', stream: 'phoenix:events-status', backend: 'phoenix:backend-status' } as const);

export function nativeShellAvailable(): boolean {
  return isTauri();
}

export function createTauriTransport(): Transport {
  return {
    async request(method: HttpMethod, path: string, body?: Record<string, unknown>): Promise<ApiResult> {
      return invoke<ApiResult>(COMMANDS.request, { method, path, body: body ?? null });
    },
    backendStatus(): Promise<unknown> {
      return invoke<unknown>(COMMANDS.status);
    },
    async subscribe(handlers: TransportHandlers): Promise<() => void> {
      const stops = await Promise.all([
        listen<unknown>(EVENTS.event, (event) => handlers.event(event.payload)),
        listen<unknown>(EVENTS.stream, (event) => handlers.stream(event.payload)),
        listen<unknown>(EVENTS.backend, (event) => handlers.backend(event.payload)),
      ]);
      return () => { for (const stop of stops) stop(); };
    },
  };
}
