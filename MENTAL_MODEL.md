# MENTAL_MODEL.md

> Before modifying PHOENIX, own the mental model.

```text
USER
  |
VOICE / TEXT
  |
UTA (when voice is used)
  |
PHOENIX — supreme orchestrator
  |
  +--> direct answer for simple work
  |
  +--> AIZEN — planning for complex work
          |
       Agent Router
          |
   specialist agents
          |
       tools/models
          |
        LEVI — verify when required
          |
     PASS | FAIL
          |   |
          |   +--> MAHORAGA — bounded adaptation/retry
          |               |
          |          repeated failure
          |               |
          |             KAIDO — degraded mode
          |
        PHOENIX
          |
         USER
```

## Core concepts

- **PHOENIX**: final routing, task state, user-facing completion, orchestration authority.
- **AIZEN**: complex planning and re-planning; not final authority.
- **Specialist agents**: bounded reasoning roles with internal ID, display alias, tools, model preference, memory scope, activation rules, and forbidden responsibilities.
- **Tools**: deterministic capabilities such as browser, terminal, filesystem, media, documents, and computer control.
- **Skills**: reusable procedures/policies.
- **Model providers**: interchangeable inference backends.
- **PUDDING**: memory.
- **VEGAPUNK**: knowledge/RAG.
- **LEVI**: verification.
- **MAHORAGA**: adaptive recovery.
- **KAIDO**: degraded-mode survival.
- **PermissionEngine**: actual security authority.

## Voice

Voice is an interface channel, not a separate assistant.

`Rise, Phoenix -> wake -> speech -> STT -> PHOENIX -> result -> TTS`

The wake phrase is not required for every follow-up while a conversation is active.

## Models

Do not map one model process per agent. Models are shared resources selected by task complexity, modality, coding need, latency budget, and hardware state.

## Reliability

Success is not "the model said it worked." Success requires observable execution evidence and verification when required.
