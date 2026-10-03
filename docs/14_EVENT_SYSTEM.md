# 14 — Event System

## Purpose

Provide observable real-time state for orchestration, UI, logging, and evaluation.

## Core events

- phoenix.started
- phoenix.ready
- phoenix.degraded
- agent.started
- agent.completed
- agent.failed
- task.created
- task.state_changed
- step.started
- step.completed
- tool.requested
- tool.started
- tool.completed
- tool.failed
- permission.requested
- permission.approved
- permission.denied
- model.loading
- model.loaded
- model.unloaded
- provider.unavailable
- verification.started
- verification.passed
- verification.failed
- recovery.started
- recovery.completed
- recovery.exhausted
- memory.created
- document.indexed
- voice.listening
- voice.speaking
- voice.interrupted

## Envelope

event_id, event_type, timestamp, task_id optional, agent_id optional, payload, severity, correlation_id.
