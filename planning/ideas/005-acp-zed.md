# 005. Agent Client Protocol adapter for Zed

- id: 005
- status: shaped
- source: own
- value: medium

## Why

LUNA has two interfaces of its own: the CLI and the web UI. Editors and agent shells already
speak the Agent Client Protocol (ACP): JSON-RPC 2.0 over stdio between a client and an agent.
Zed hosts ACP agents in its Agent Panel with tool cards, permission prompts, diffs, inline
Keep/Reject review, modes, slash commands and a stop button. Toad offers the same in a
terminal. One adapter gives LUNA all of these clients without new UI code.

## What changes

A `luna-acp` entry point built on the `agent-client-protocol` Python SDK. It wraps
`SessionManager` and `AgentSession` and maps:

- `session/new` → `create_session`; `session/prompt` → `run_turn`, answered with `end_turn`
  on `turn_done`; `session/cancel` → `cancel_turn`, answered with `cancelled`.
- `messages` events → `agent_message_chunk`; model reasoning → `agent_thought_chunk`.
- `updates` events → `tool_call` / `tool_call_update` with status and kind (`read`, `edit`,
  `search`, …). Edits carry `diff` content.
- HITL confirm → `session/request_permission` (`allow_once`, `allow_always`, `reject_once`).
- `/plan` `/act` → session modes and `session/set_mode`. `/confirm` `/accept` → a config
  option or `allow_always`. `/cp` `/undo` `/fs` → `available_commands_update`.
- A subagent run → one `tool_call` with progress updates. ACP has no nesting.

Logs go to the file, never to stdout: stdout carries the protocol.

Decide before building:

- Working directory. An ACP client passes the project `cwd` and expects edits there. LUNA
  works in its own session workspace. Either work in the project folder, or keep the
  workspace and accept that Zed's inline review does not apply.
- HITL ask and select. There is no stable free-text question in ACP. Until there is, ask in
  the message and end the turn, and map select to permission options where it fits.
- Session load. Replaying history for `session/load` depends on checkpoints.

Zed connects it as a custom agent:

```json
{ "agent_servers": { "LUNA": { "type": "custom", "command": "luna-acp" } } }
```

## Why this is LUNA

The adapter is a third consumer of the same `AgentSession` event contract, next to the CLI
and the server. The core does not change. If the contract maps cleanly to ACP, that confirms
the boundary is right.

## Out of scope

- Replacing the CLI from [004](004-cli-prompt-toolkit-rich.md) or the web UI.
- Using the client's terminal (`terminal/*`) instead of the host shell tool.
- Publishing LUNA to the ACP registry.
- HTTP or WebSocket ACP transports.
