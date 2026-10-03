# 07 — Tool System

A tool is a deterministic capability exposed through ToolRegistry. A tool is not an agent.

## Tool contract

Every tool declares:
- tool_id
- description
- input/output schema
- risk level
- permission requirement
- timeout
- cancellation support
- audit behavior
- supported platform/environment

## Core families

- filesystem
- terminal/process
- browser
- web search/page extraction
- screenshots
- computer input
- app/window control
- clipboard
- document parse/generate
- Spotify
- YouTube
- notifications

## Flow

Agent -> ToolRegistry -> validate -> PermissionEngine -> execute -> structured result -> audit/event -> caller.

## Result states

success, partial_success, denied, timeout, cancelled, failure, unavailable_dependency.
