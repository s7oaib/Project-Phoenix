// PHX-107 (ADR-092): concrete port adapters over the PHX-106 native transport.
//
// The webview never talks to the backend itself. A Transport (tauri.ts in the app, a mock in
// tests) forwards allowlisted REST calls through the Rust shell, which adds the per-launch
// credential; this module never sees it. REST is the source of truth: content-free events only
// schedule refetches and add live IDs/states/timings for the Task Graph, Agent Network and
// Observability views. Every port object is a fresh plain data snapshot shaped exactly as
// App.tsx's readers admit it. Memory, Files and Voice stay unavailable (no backend API yet).

import { agentRoster, taskStates } from '../App';
import type {
  AgentId, AgentPort, AgentStatus, ChatMessage, ChatPort, DecisionIntent, MetricDisplayEntry,
  ModelDisplayEntry, ModelPort, ObservabilityPort, PermissionEntry, PermissionEvaluation, PermissionPort,
  TaskGraphPort, TaskState, TaskStep, TraceDisplayEntry,
} from '../App';

export type HttpMethod = 'GET' | 'POST' | 'PATCH';
export type ApiResult = { readonly status: number; readonly body: unknown };
export type BackendState = { readonly state: 'starting' | 'ready' | 'degraded'; readonly code?: string };
export type StreamState = { readonly connected: boolean; readonly code: number | null };
export type TransportHandlers = {
  event(value: unknown): void;
  stream(value: unknown): void;
  backend(value: unknown): void;
};
export interface Transport {
  request(method: HttpMethod, path: string, body?: Record<string, unknown>): Promise<ApiResult>;
  backendStatus(): Promise<unknown>;
  subscribe(handlers: TransportHandlers): Promise<() => void>;
}
export type Scheduler = {
  setTimeout(callback: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
  setInterval(callback: () => void, ms: number): unknown;
  clearInterval(handle: unknown): void;
};

export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export const MESSAGE_PART = 4000;
export const MESSAGE_WINDOW = 64;
export const RING = 64;
export const STEP_LIMIT_SECONDS = 300; // RuntimeLimits.step_timeout_seconds default (PHX-029)
export const REFRESH_DELAY_MS = 120;
export const POLL_MS = 3000;
/** 'auto' omits `path`, so PHOENIX triages the request (PHX-105); the others are explicit overrides. */
export type MissionPath = 'auto' | 'direct' | 'complex';
export const ACTIVE_STATUSES = Object.freeze(['queued', 'running']);
export const TERMINAL_STATUSES = Object.freeze(['completed', 'failed', 'cancelled', 'superseded']);

export class ApiError extends Error {
  constructor(readonly status: number, readonly code: string) {
    super(code);
  }
}

// ---- strict readers for backend JSON (shape, not trust)

type Json = Record<string, unknown>;
function isObject(value: unknown): value is Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function str(value: unknown, limit = 4096): string | undefined {
  return typeof value === 'string' && value.length <= limit ? value : undefined;
}
function id(value: unknown): string | undefined {
  return typeof value === 'string' && UUID.test(value) ? value : undefined;
}
/** API timestamps are UTC; normalize to the `...Z` form App.tsx admits. */
export function utc(value: unknown): string | undefined {
  if (typeof value !== 'string' || value.length > 64) return undefined;
  const match = value.match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d{1,9})?(Z|\+00:00)?$/);
  return match ? `${match[1]}${match[2] ?? ''}Z` : undefined;
}
function items(body: unknown): unknown[] {
  return isObject(body) && Array.isArray(body.items) ? body.items : [];
}
function total(body: unknown): number {
  return isObject(body) && typeof body.total === 'number' && Number.isSafeInteger(body.total) && body.total >= 0 ? body.total : 0;
}
export function errorCode(body: unknown): string {
  const error = isObject(body) ? body.error : undefined;
  const code = isObject(error) ? str(error.code, 64) : undefined;
  return code ?? 'request_failed';
}

export type PendingPermission = {
  requestId: string; missionId: string; toolId: string; action: string; resource: string;
  riskLevel: string | null; requestedAt: string;
};
export function readPermission(value: unknown): PendingPermission | undefined {
  if (!isObject(value)) return undefined;
  const requestId = id(value.request_id);
  const missionId = id(value.mission_id);
  const toolId = str(value.tool_id, 128);
  const action = str(value.action, 256);
  const resource = str(value.resource, 512); // App admits at most 512; longer ones are counted as hidden
  const requestedAt = utc(value.requested_at);
  const risk = value.risk_level === null ? null : str(value.risk_level, 8);
  if (!requestId || !missionId || !toolId || !action || !resource || !requestedAt || risk === undefined || value.status !== 'pending') return undefined;
  return { requestId, missionId, toolId, action, resource, riskLevel: risk, requestedAt };
}

export type MissionView = {
  missionId: string; conversationId: string; status: string;
  /** PHX-105: null until triage decides; triageOutcome says who decided. */
  path: 'direct' | 'complex' | null; triageOutcome: 'decided' | 'fallback' | 'client' | null; failureCode: string | null;
  rerunOf: string | null; rerunId: string | null; taskId: string | null; taskStates: readonly string[];
  permissionRequestId: string | null;
};
export function readMission(value: unknown): MissionView | undefined {
  if (!isObject(value)) return undefined;
  const missionId = id(value.mission_id);
  const conversationId = id(value.conversation_id);
  const status = str(value.status, 32);
  if (!missionId || !conversationId || !status) return undefined;
  const states = Array.isArray(value.task_states) ? value.task_states.filter((state): state is string => typeof state === 'string') : [];
  const request = isObject(value.permission_request) ? id(value.permission_request.request_id) ?? null : null;
  return {
    missionId, conversationId, status,
    path: value.path === 'direct' || value.path === 'complex' ? value.path : null,
    triageOutcome: value.triage_outcome === 'decided' || value.triage_outcome === 'fallback' || value.triage_outcome === 'client' ? value.triage_outcome : null,
    failureCode: str(value.failure_code, 64) ?? null, rerunOf: id(value.rerun_of) ?? null,
    rerunId: id(value.rerun_id) ?? null, taskId: id(value.task_id) ?? null, taskStates: states,
    permissionRequestId: request,
  };
}

/** User messages and PHOENIX answers as App ChatMessages; long answers become ordered parts. */
export function toChatMessages(body: unknown): ChatMessage[] {
  const messages: ChatMessage[] = [];
  for (const entry of items(body)) {
    if (!isObject(entry)) continue;
    const messageId = id(entry.message_id);
    const text = typeof entry.text === 'string' ? entry.text : undefined;
    const role = entry.role === 'user' ? 'user' : entry.role === 'phoenix' ? 'assistant' : undefined;
    if (!messageId || !role || text === undefined || text.trim().length === 0) continue;
    const parts = Math.ceil(text.length / MESSAGE_PART);
    for (let part = 0; part < parts; part += 1) {
      const content = text.slice(part * MESSAGE_PART, (part + 1) * MESSAGE_PART);
      if (content.trim().length === 0) continue;
      messages.push({ id: parts === 1 ? messageId : `${messageId}:${part + 1}`, role, content });
    }
  }
  return messages.slice(-MESSAGE_WINDOW);
}

const riskReason: Readonly<Record<string, PermissionEvaluation['reasonCode']>> = {
  P1: 'p1_scope_required', P2: 'p2_approval_required', P3: 'p3_approval_required', P4: 'p4_explicit_approval_required',
};

/** Pending backend requests as PermissionEntries. Only P1-P4 pending requests carry the
 *  approval_required evaluation that enables Approve/Deny; PermissionEngine stays authoritative. */
export function toPermissionEntries(pending: readonly PendingPermission[], actors: ReadonlyMap<string, string>): PermissionEntry[] {
  return pending.slice(0, 64).map((request) => {
    const actorId = actors.get(request.requestId) ?? 'phoenix';
    const base = {
      requestId: request.requestId, actorId, toolId: request.toolId, action: request.action, resource: request.resource,
      reason: `Mission ${request.missionId} needs ${request.action} through ${request.toolId}. Approve stores a grant for this launch (session) and queues the linked re-run; Deny fails the mission.`,
      requestedAt: request.requestedAt,
    };
    const reasonCode = request.riskLevel === null ? undefined : riskReason[request.riskLevel];
    if (!reasonCode || request.riskLevel === null) return { request: base };
    return {
      request: base,
      evaluation: {
        requestId: request.requestId, actorId, toolId: request.toolId,
        riskLevel: request.riskLevel as 'P1' | 'P2' | 'P3' | 'P4',
        permissionRequirement: `User approval for ${request.action} on this exact resource (${request.riskLevel})`,
        status: 'approval_required' as const, reasonCode,
      },
    };
  });
}

export function toModelEntries(body: unknown): ModelDisplayEntry[] | undefined {
  if (!Array.isArray(body)) return undefined;
  const entries: ModelDisplayEntry[] = [];
  for (const entry of body.slice(0, 64)) {
    if (!isObject(entry)) return undefined;
    const providerId = str(entry.provider_id, 128);
    const modelId = str(entry.model_id, 128);
    if (!providerId?.trim() || !modelId?.trim()) return undefined;
    if (entries.some((item) => item.providerId === providerId && item.modelId === modelId)) continue;
    const status = entry.provider_status;
    const limit = entry.context_limit_tokens;
    entries.push({
      providerId, modelId,
      ...(status === 'healthy' || status === 'degraded' || status === 'unavailable' ? { providerStatus: status } : {}),
      ...(typeof limit === 'number' && Number.isSafeInteger(limit) && limit > 0 ? { contextLimitTokens: limit } : {}),
    });
  }
  return entries;
}

// ---- live evidence from content-free events

export type EventEnvelope = {
  eventId: string; eventType: string; timestamp: string; taskId: string | null; agentId: string | null;
  payload: Json; correlationId: string | null;
};
export function readEvent(value: unknown): EventEnvelope | undefined {
  if (!isObject(value) || !isObject(value.payload)) return undefined;
  const eventId = str(value.event_id, 128);
  const eventType = str(value.event_type, 64);
  const timestamp = utc(value.timestamp);
  if (!eventId?.trim() || !eventType || !timestamp) return undefined;
  return {
    eventId, eventType, timestamp, payload: value.payload,
    taskId: id(value.task_id) ?? null, agentId: str(value.agent_id, 64) ?? null, correlationId: id(value.correlation_id) ?? null,
  };
}

const knownAgents: ReadonlySet<string> = new Set(agentRoster.map((agent) => agent.internalId));
function agent(value: string | null | undefined): AgentId | undefined {
  return value && knownAgents.has(value) ? value as AgentId : undefined;
}
function short(missionId: string): string {
  return missionId.slice(0, 8);
}

export type StepStatus = 'running' | 'tool' | 'waiting_permission' | 'verifying' | 'completed' | 'failed';
export type StepEvidence = {
  stepId: string; owner?: AgentId; domain?: string; final: boolean; status: StepStatus;
  toolId?: string; code?: string; durationMs?: number;
};
export type MissionEvidence = {
  missionId: string; taskId?: string; taskState?: TaskState; missionStatus?: string; steps: StepEvidence[];
};

const stepLabels: Readonly<Record<StepStatus, string>> = {
  running: 'running', tool: 'calling a tool', waiting_permission: 'waiting for your permission',
  verifying: 'LEVI verifying', completed: 'completed (LEVI passed)', failed: 'failed',
};
export function toTaskStep(step: StepEvidence): TaskStep {
  const detail = step.status === 'failed' && step.code ? ` (${step.code})` :
    step.status === 'completed' && step.durationMs !== undefined ? ` in ${step.durationMs} ms` :
    (step.status === 'tool' || step.status === 'waiting_permission') && step.toolId ? ` (${step.toolId})` : '';
  return {
    stepId: step.stepId,
    ownerAgentId: step.owner ?? 'phoenix',
    objective: `${step.final ? 'Final answer' : `${step.domain ?? 'Plan'}`} step. Live status: ${stepLabels[step.status]}${detail}. Objective text is not published by the content-free event stream.`,
    dependsOn: [],
    inputRefs: [],
    expectedOutput: 'Not published by events; the verified answer appears in Chat.',
    completionRule: 'step.completed is published only after LEVI verification passes.',
    timeoutSeconds: STEP_LIMIT_SECONDS,
  };
}

function stepOrder(step: StepEvidence): number {
  if (step.stepId === 'final') return 1000;
  const number = Number(step.stepId.slice(1));
  return Number.isFinite(number) ? number : 999;
}

export function toTaskGraph(evidence: MissionEvidence | undefined, mission: MissionView | undefined): TaskGraphPort['graph'] {
  if (!evidence || evidence.steps.length === 0) return undefined;
  const steps = [...evidence.steps].sort((a, b) => stepOrder(a) - stepOrder(b)).slice(0, 40).map(toTaskStep);
  const detailState = mission?.missionId === evidence.missionId ? mission.taskStates[mission.taskStates.length - 1] : undefined;
  const state = taskStates.find((item) => item === (detailState ?? evidence.taskState));
  const taskId = (mission?.missionId === evidence.missionId ? mission.taskId : undefined) ?? evidence.taskId ?? evidence.missionId;
  return { taskId, ...(state ? { taskState: state } : {}), steps };
}

const traceKinds: ReadonlyArray<[string, TraceDisplayEntry['kind']]> = [
  ['agent.', 'agent'], ['step.', 'agent'], ['tool.', 'tool'], ['permission.', 'permission'],
  ['verification.', 'verification'], ['recovery.', 'recovery'], ['task.', 'decision'],
];
export function toTrace(event: EventEnvelope): TraceDisplayEntry {
  const failed = event.eventType.endsWith('.failed') || typeof event.payload.failure_code === 'string';
  const kind = failed && !event.eventType.startsWith('verification.') ? 'failure' :
    traceKinds.find(([prefix]) => event.eventType.startsWith(prefix))?.[1] ?? 'decision';
  const fields = Object.entries(event.payload)
    .filter(([, value]) => typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean')
    .slice(0, 8).map(([key, value]) => `${key}=${String(value)}`).join(' ');
  const stepId = str(event.payload.step_id, 16);
  return {
    id: event.eventId, timestamp: event.timestamp, kind,
    summary: `${event.eventType}${fields ? ` ${fields}` : ''}`.slice(0, 1000),
    ...(event.correlationId ? { correlationId: event.correlationId } : {}),
    ...(event.taskId ? { taskId: event.taskId } : {}),
    ...(stepId ? { stepId } : {}),
    ...(agent(event.agentId) ? { actorId: event.agentId as string } : {}),
  };
}

export function toMetric(event: EventEnvelope): MetricDisplayEntry | undefined {
  const duration = event.payload.duration_ms;
  const base = { id: `${event.eventId}:metric`, recordedAt: event.timestamp };
  if (event.eventType === 'step.completed' && typeof duration === 'number') return { ...base, name: 'task_step_latency', value: duration, unit: 'ms' };
  if (event.eventType === 'verification.passed' || event.eventType === 'verification.failed') {
    return { ...base, name: 'verification_result', value: event.eventType === 'verification.passed' ? 'passed' : 'failed' };
  }
  if (event.eventType === 'tool.completed' || event.eventType === 'tool.failed') {
    return { ...base, name: 'tool_success_failure', value: str(event.payload.tool_status, 32) ?? (event.eventType === 'tool.completed' ? 'success' : 'failure') };
  }
  return undefined;
}

// ---- the store behind every port

export type ConnectionView = {
  backend: BackendState;
  stream: StreamState;
  workspace: { root: string | null; status: string } | undefined;
  workspaceMessage: string;
  hiddenPermissions: number;
  path: MissionPath;
  mission: MissionView | undefined;
  missionMessage: string;
};
export type PortsView = {
  chatPort: ChatPort; taskPort: TaskGraphPort; agentPort: AgentPort; permissionPort: PermissionPort;
  modelPort: ModelPort; observabilityPort: ObservabilityPort; connection: ConnectionView;
};

type Activity = { state: AgentStatus['state']; missionId: string; currentTask?: string };

export class PhoenixStore {
  private backend: BackendState = { state: 'starting' };
  private stream: StreamState = { connected: false, code: null };
  private conversationId: string | undefined;
  private messages: ChatMessage[] = [];
  private activeMissionId: string | undefined;
  private mission: MissionView | undefined;
  private pending: PendingPermission[] = [];
  private hiddenPermissions = 0;
  private actors = new Map<string, string>();
  private activity = new Map<AgentId, Activity>();
  private evidence = new Map<string, MissionEvidence>();
  private latestEvidence: string | undefined;
  private traces: TraceDisplayEntry[] = [];
  private metrics: MetricDisplayEntry[] = [];
  private models: ModelDisplayEntry[] | undefined;
  private workspace: ConnectionView['workspace'];
  private workspaceMessage = '';
  private missionMessage = '';
  private path: MissionPath = 'auto';
  private due = new Set<'conversation' | 'mission' | 'permissions' | 'models' | 'settings'>();
  private timer: unknown;
  private poller: unknown;
  private listeners = new Set<() => void>();
  private unsubscribe: (() => void) | undefined;
  private stopped = false;
  private view: PortsView;

  constructor(private readonly transport: Transport, private readonly scheduler: Scheduler) {
    this.sendText = this.sendText.bind(this);
    this.submitDecision = this.submitDecision.bind(this);
    this.view = this.build();
  }

  // -- useSyncExternalStore surface
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  getSnapshot = (): PortsView => this.view;

  private changed(): void {
    this.view = this.build();
    for (const listener of this.listeners) listener();
  }

  async start(): Promise<void> {
    this.unsubscribe = await this.transport.subscribe({
      event: (value) => this.onEvent(value),
      stream: (value) => this.onStream(value),
      backend: (value) => this.onBackend(value),
    });
    if (this.stopped) { this.unsubscribe(); return; }
    this.poller = this.scheduler.setInterval(() => {
      if (this.backend.state === 'ready' && this.mission && !TERMINAL_STATUSES.includes(this.mission.status)) this.schedule('mission', 'permissions', 'conversation');
    }, POLL_MS);
    this.onBackend(await this.transport.backendStatus().catch(() => ({ state: 'degraded', code: 'ipc_unavailable' })));
  }

  stop(): void {
    this.stopped = true;
    this.unsubscribe?.();
    if (this.timer !== undefined) this.scheduler.clearTimeout(this.timer);
    if (this.poller !== undefined) this.scheduler.clearInterval(this.poller);
  }

  // -- transport inputs
  onBackend(value: unknown): void {
    if (!isObject(value)) return;
    const state = value.state === 'starting' || value.state === 'ready' || value.state === 'degraded' ? value.state : undefined;
    if (!state) return;
    const code = str(value.code, 64);
    const wasReady = this.backend.state === 'ready';
    this.backend = { state, ...(code ? { code } : {}) };
    if (state === 'ready' && !wasReady) this.schedule('conversation', 'mission', 'permissions', 'models', 'settings');
    if (state !== 'ready') this.stream = { connected: false, code: null };
    this.changed();
  }

  onStream(value: unknown): void {
    if (!isObject(value) || typeof value.connected !== 'boolean') return;
    const code = typeof value.code === 'number' ? value.code : null;
    this.stream = { connected: value.connected, code };
    // Any (re)connection or close may have missed events: reload everything over REST.
    this.schedule('conversation', 'mission', 'permissions', 'models', 'settings');
    this.changed();
  }

  onEvent(value: unknown): void {
    const event = readEvent(value);
    if (!event) return;
    this.traces = [...this.traces, toTrace(event)].slice(-RING);
    const metric = toMetric(event);
    if (metric) this.metrics = [...this.metrics, metric].slice(-RING);
    const missionId = event.correlationId;
    if (missionId) this.applyMissionEvent(missionId, event);
    this.changed();
  }

  private evidenceFor(missionId: string): MissionEvidence {
    let found = this.evidence.get(missionId);
    if (!found) {
      found = { missionId, steps: [] };
      this.evidence.set(missionId, found);
      if (this.evidence.size > 8) this.evidence.delete(this.evidence.keys().next().value as string);
    }
    return found;
  }

  private step(evidence: MissionEvidence, stepId: string): StepEvidence {
    let found = evidence.steps.find((item) => item.stepId === stepId);
    if (!found) {
      found = { stepId, final: stepId === 'final', status: 'running' };
      evidence.steps.push(found);
      this.latestEvidence = evidence.missionId;
    }
    return found;
  }

  private applyMissionEvent(missionId: string, event: EventEnvelope): void {
    const payload = event.payload;
    const evidence = this.evidenceFor(missionId);
    if (event.taskId) evidence.taskId = event.taskId;
    const stepId = typeof payload.step_id === 'string' && /^(s[1-9][0-9]?|final)$/.test(payload.step_id) ? payload.step_id : undefined;
    const owner = agent(event.agentId);
    const role = str(payload.role, 32);
    switch (event.eventType) {
      case 'task.state_changed': {
        if (payload.scope === 'task') {
          evidence.taskState = taskStates.find((state) => state === payload.to_state);
          break;
        }
        const status = str(payload.mission_status, 32);
        if (!status) break;
        evidence.missionStatus = status;
        const rerunOf = id(payload.rerun_of);
        if (rerunOf && rerunOf === this.activeMissionId) this.activeMissionId = missionId; // linked re-run
        if (status === 'running' && missionId === this.activeMissionId) {
          this.activity.set('phoenix', { state: 'working', missionId, currentTask: `Mission ${short(missionId)} running` });
        }
        if (TERMINAL_STATUSES.includes(status) || status === 'waiting_permission') {
          for (const [agentId, activity] of this.activity) {
            if (activity.missionId !== missionId) continue;
            if (status === 'waiting_permission' && activity.state === 'waiting_permission') continue;
            this.activity.set(agentId, { state: 'idle', missionId });
          }
          if (status === 'waiting_permission') this.activity.set('phoenix', { state: 'waiting_permission', missionId, currentTask: `Mission ${short(missionId)} waits for your decision` });
        }
        this.schedule('mission', 'permissions', 'conversation');
        break;
      }
      case 'agent.started':
        if (owner) this.activity.set(owner, { state: 'working', missionId, currentTask: `Mission ${short(missionId)} · ${role ?? 'work'}${stepId ? ` · step ${stepId}` : ''}` });
        if (stepId && role !== 'assignment') { const step = this.step(evidence, stepId); step.owner ??= owner; }
        break;
      case 'agent.completed':
        if (owner) {
          this.activity.set(owner, payload.outcome === 'waiting_permission'
            ? { state: 'waiting_permission', missionId, currentTask: `Mission ${short(missionId)}${stepId ? ` · step ${stepId}` : ''} waits for permission` }
            : { state: 'idle', missionId });
        }
        break;
      case 'agent.failed':
        if (owner) this.activity.set(owner, { state: 'idle', missionId });
        if (stepId) Object.assign(this.step(evidence, stepId), { status: 'failed', code: str(payload.code, 64) });
        break;
      case 'step.started':
        if (stepId) {
          const step = this.step(evidence, stepId);
          step.owner = owner ?? step.owner;
          step.domain = str(payload.domain, 32) ?? step.domain;
          step.final = payload.final === true || step.final;
          if (step.status !== 'completed') step.status = 'running';
        }
        break;
      case 'tool.requested':
      case 'tool.started':
        if (stepId) Object.assign(this.step(evidence, stepId), { status: 'tool', toolId: str(payload.tool_id, 64) });
        break;
      case 'permission.requested': {
        const requestId = id(payload.permission_request_id);
        if (requestId && owner) this.actors.set(requestId, owner);
        if (stepId) Object.assign(this.step(evidence, stepId), { status: 'waiting_permission', toolId: str(payload.tool_id, 64) });
        this.schedule('permissions', 'mission');
        break;
      }
      case 'tool.failed':
        if (stepId) Object.assign(this.step(evidence, stepId), { status: 'failed', code: str(payload.code, 64) });
        break;
      case 'verification.started':
        if (stepId) this.step(evidence, stepId).status = 'verifying';
        break;
      case 'verification.failed':
        if (stepId) Object.assign(this.step(evidence, stepId), { status: 'failed', code: str(payload.code, 64) ?? 'verification_failed' });
        break;
      case 'step.completed':
        if (stepId) Object.assign(this.step(evidence, stepId), { status: 'completed', durationMs: typeof payload.duration_ms === 'number' ? payload.duration_ms : undefined });
        break;
      default:
        break;
    }
  }

  // -- REST refresh, coalesced
  schedule(...what: Array<'conversation' | 'mission' | 'permissions' | 'models' | 'settings'>): void {
    for (const item of what) this.due.add(item);
    if (this.timer !== undefined || this.stopped) return;
    this.timer = this.scheduler.setTimeout(() => {
      this.timer = undefined;
      void this.refresh();
    }, REFRESH_DELAY_MS);
  }

  async refresh(): Promise<void> {
    if (this.backend.state !== 'ready') return;
    const due = new Set(this.due);
    this.due.clear();
    await Promise.all([
      due.has('settings') ? this.loadSettings() : undefined,
      due.has('models') ? this.loadModels() : undefined,
      due.has('permissions') ? this.loadPermissions() : undefined,
      due.has('mission') ? this.loadMission() : undefined,
    ].map((task) => task?.catch(() => undefined)));
    if (due.has('conversation')) await this.loadConversation().catch(() => undefined);
    this.changed();
  }

  private async get(path: string): Promise<unknown> {
    const result = await this.transport.request('GET', path);
    if (result.status !== 200) throw new ApiError(result.status, errorCode(result.body));
    return result.body;
  }

  private async loadSettings(): Promise<void> {
    const body = await this.get('/settings');
    const workspace = isObject(body) && isObject(body.workspace) ? body.workspace : undefined;
    const status = workspace ? str(workspace.status, 32) : undefined;
    if (workspace && status) this.workspace = { root: str(workspace.root) ?? null, status };
  }

  private async loadModels(): Promise<void> {
    this.models = toModelEntries(await this.get('/models'));
  }

  private async loadPermissions(): Promise<void> {
    const body = await this.get('/permissions?limit=64');
    const all = items(body);
    this.pending = all.map(readPermission).filter((entry): entry is PendingPermission => entry !== undefined);
    this.hiddenPermissions = all.length - this.pending.length;
  }

  private async loadMission(): Promise<void> {
    if (!this.activeMissionId) return;
    const mission = readMission(await this.get(`/missions/${this.activeMissionId}`));
    if (!mission) return;
    this.mission = mission;
    // Follow a linked re-run the user started from the Permissions view.
    if (mission.rerunId && mission.status === 'superseded') {
      this.activeMissionId = mission.rerunId;
      this.schedule('mission');
    }
  }

  private async loadConversation(): Promise<void> {
    if (!this.conversationId) {
      const first = await this.get('/conversations?limit=1');
      const count = total(first);
      if (count === 0) return;
      const last = items(await this.get(`/conversations?limit=1&offset=${count - 1}`))[0];
      const conversationId = isObject(last) ? id(last.conversation_id) : undefined;
      if (!conversationId) return;
      this.conversationId = conversationId;
      if (!this.activeMissionId) {
        const missions = await this.get(`/missions?conversation_id=${conversationId}&limit=1`);
        const missionCount = total(missions);
        const latest = missionCount > 0 ? items(await this.get(`/missions?conversation_id=${conversationId}&limit=1&offset=${missionCount - 1}`))[0] : undefined;
        const missionId = isObject(latest) ? id(latest.mission_id) : undefined;
        if (missionId) { this.activeMissionId = missionId; this.schedule('mission'); }
      }
    }
    const path = `/conversations/${this.conversationId}/messages`;
    const count = total(await this.get(`${path}?limit=1`));
    this.messages = toChatMessages(await this.get(`${path}?limit=${MESSAGE_WINDOW}&offset=${Math.max(0, count - MESSAGE_WINDOW)}`));
  }

  // -- port operations
  async sendText(text: string): Promise<void> {
    const body: Record<string, unknown> = { text };
    if (this.conversationId) body.conversation_id = this.conversationId;
    if (this.path !== 'auto') body.path = this.path;
    const result = await this.transport.request('POST', '/chat', body);
    if (result.status !== 202 || !isObject(result.body)) throw new ApiError(result.status, errorCode(result.body));
    const missionId = id(result.body.mission_id);
    const conversationId = id(result.body.conversation_id);
    if (!missionId || !conversationId) throw new ApiError(result.status, 'invalid_response');
    this.conversationId = conversationId;
    this.activeMissionId = missionId;
    this.missionMessage = '';
    this.mission = undefined;
    this.schedule('conversation', 'mission');
    this.changed();
  }

  async submitDecision(requestId: string, decision: DecisionIntent): Promise<void> {
    if (!UUID.test(requestId) || (decision !== 'approved' && decision !== 'denied')) throw new ApiError(0, 'invalid_request');
    const result = decision === 'approved'
      ? await this.transport.request('POST', `/permissions/${requestId}/approve`, { duration: 'session' })
      : await this.transport.request('POST', `/permissions/${requestId}/deny`, {});
    const expected = decision === 'approved' ? 202 : 200;
    if (result.status !== expected) throw new ApiError(result.status, errorCode(result.body));
    if (decision === 'approved' && isObject(result.body)) {
      const rerun = id(result.body.mission_id);
      const superseded = id(result.body.superseded_mission_id);
      if (rerun && (!this.activeMissionId || superseded === this.activeMissionId)) this.activeMissionId = rerun;
    }
    this.pending = this.pending.filter((entry) => entry.requestId !== requestId);
    this.schedule('permissions', 'mission', 'conversation');
    this.changed();
  }

  async setWorkspace(root: string): Promise<void> {
    const value = root.trim();
    if (!value) { this.workspaceMessage = 'Enter an absolute folder path.'; this.changed(); return; }
    const result = await this.transport.request('PATCH', '/settings', { workspace_root: value }).catch(() => ({ status: 0, body: undefined }));
    if (result.status === 200) {
      this.workspaceMessage = 'Workspace saved. Missions that start from now on may read documents in it, with your permission.';
      this.schedule('settings');
    } else {
      this.workspaceMessage = workspaceErrors[errorCode(result.body)] ?? 'The workspace was not saved. Check the backend connection and try again.';
    }
    this.changed();
  }

  setPath(path: MissionPath): void {
    this.path = path;
    this.changed();
  }

  async missionAction(action: 'cancel' | 'run-again'): Promise<void> {
    const mission = this.mission;
    if (!mission) return;
    const result = await this.transport.request('POST', `/missions/${mission.missionId}/${action}`).catch(() => ({ status: 0, body: undefined }));
    if (result.status === 202) {
      if (action === 'run-again' && isObject(result.body)) {
        const rerun = id(result.body.mission_id);
        if (rerun) this.activeMissionId = rerun;
      }
      this.missionMessage = action === 'cancel' ? 'Cancellation requested.' : 'Linked re-run queued.';
    } else {
      this.missionMessage = `${action === 'cancel' ? 'Cancel' : 'Run again'} was refused (${errorCode(result.body)}).`;
    }
    this.schedule('mission', 'conversation');
    this.changed();
  }

  // -- snapshots
  private build(): PortsView {
    const status: ChatPort['status'] = this.backend.state === 'ready' ? 'connected' : this.backend.state === 'degraded' ? 'degraded' : 'unavailable';
    const agents = [...this.activity.entries()].slice(0, 30).map(([internalId, activity]) => ({
      internalId, state: activity.state, ...(activity.currentTask ? { currentTask: activity.currentTask.slice(0, 240) } : {}),
    }));
    const evidence = this.evidence.get(this.activeMissionId ?? '')?.steps.length ? this.evidence.get(this.activeMissionId ?? '') : this.evidence.get(this.latestEvidence ?? '');
    const graph = status === 'connected' ? toTaskGraph(evidence, this.mission) : undefined;
    return {
      chatPort: { status, messages: [...this.messages], sendText: this.sendText },
      taskPort: graph ? { status, graph } : { status },
      agentPort: { status, agents },
      permissionPort: { status, requests: toPermissionEntries(this.pending, this.actors), submitDecision: this.submitDecision },
      modelPort: this.models ? { status, entries: [...this.models] } : { status: status === 'connected' ? 'unavailable' : status, entries: [] },
      observabilityPort: { status, metrics: [...this.metrics], logs: [], traces: [...this.traces] },
      connection: {
        backend: this.backend, stream: this.stream, workspace: this.workspace, workspaceMessage: this.workspaceMessage,
        hiddenPermissions: this.hiddenPermissions,
        path: this.path, mission: this.mission, missionMessage: this.missionMessage,
      },
    };
  }
}

export const workspaceErrors: Readonly<Record<string, string>> = {
  workspace_invalid: 'Not accepted: use an absolute local folder path (no UNC, device path or "..").',
  workspace_not_found: 'That folder does not exist.',
  workspace_not_directory: 'That path is a file, not a folder.',
  workspace_link: 'Links, junctions and reparse points are not accepted; use the real folder path.',
  workspace_drive_root: 'A whole drive cannot be the workspace; choose a folder.',
  workspace_system_directory: 'System folders cannot be the workspace.',
  workspace_app_data: 'Folders inside AppData cannot be the workspace.',
  workspace_data_directory: "The workspace cannot overlap PHOENIX's data directory.",
  invalid_request: 'The folder path was not accepted.',
  service_unavailable: 'The backend is starting or stopping; try again shortly.',
};
