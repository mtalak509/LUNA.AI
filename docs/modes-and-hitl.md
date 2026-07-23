# Modes and questions

LUNA gives you two independent switches to control how much freedom the agent has, plus a way for
the agent to stop and ask you something. Together these are the "human stays in control" parts of
the system.

## Two switches

The two switches are independent — all four combinations are valid — and both can be changed at
any time (a change takes effect on the next turn).

### Permission: Plan or Act

This controls **whether the agent may change anything**.

- **Plan** — read-only. The agent can investigate, inspect documents, and propose what it would
  do, but it cannot write. This is the mode for "look into this and tell me your plan" before you
  let it loose.
- **Act** — full access. The agent can read and write.

Plan is enforced in two layers (in the permission middleware, see [runtime.md](runtime.md)): the
write tools are hidden from the model so it isn't tempted, and if the model somehow calls one
anyway, the call is blocked and returned as an error. Belt and suspenders, because a model is
probabilistic and might try a tool it saw named in its prompt.

### Decision: Confirm or Accept-all

This controls **whether writes need your approval**.

- **Confirm** — before any write tool actually runs, the turn pauses and shows you the action
  (the tool name and its arguments). You approve or reject. Reject, and the model gets an "action
  declined" result and moves on.
- **Accept-all** — writes run without asking.

The key design point: this is a gate on the *execution* of write tools, not a separate "please
confirm" tool the model is supposed to call. An earlier version did use such a tool and it proved
unreliable — the model would sometimes just skip it, which quietly defeated the safety check. Now
the gate sits on the tools themselves and doesn't depend on the model cooperating.

### How to flip them

In the CLI: `/plan`, `/act`, `/confirm`, `/accept`. Over HTTP: `POST /sessions/{id}/mode`. See
[interfaces.md](interfaces.md). Starting values differ slightly by front end (the CLI starts in
`act` / `accept_all` for a frictionless dev loop; the server starts new sessions in `act` /
`confirm`).

## When the agent asks you a question

Separately from the Confirm gate, the agent can deliberately ask you something mid-turn using one
of two tools:

- **`ask_user`** — an open question with a free-text answer. Used when it's genuinely missing
  information, hit a real fork in meaning, or found something ambiguous.
- **`select_from_options`** — a short list of choices (up to 10) to pick from. The choices must
  come from a previous tool result — the agent isn't allowed to invent them — and it names the
  source tool. You can usually also answer in free text instead of picking.

Either way, the turn **parks**: the tool call suspends and waits for your answer, then resumes
exactly where it left off. Nothing is lost; the agent's position in its work is preserved because
it's just an awaiting coroutine, not a torn-down process.

You answer in the CLI at the prompt, or over HTTP with `POST /sessions/{id}/hitl/respond`.

### Questions from a subagent

A subagent can ask a question too, but it never talks to you directly. Its question is routed up
and surfaced through the main agent's stream, and your answer flows back down to it. The subagent
sees who answered — a real user answer is tagged `source: "user"`. (There's a designed-but-deferred
path for the main agent to answer a subagent's question itself when it can infer the answer; today
subagent questions are always forwarded to you. See TD-11 in [tech-debt.md](tech-debt.md).)

## The "pointer" hint

There's one more small piece of context the front end can send: a **pointer** to the part of a
document the user currently has open in the UI (a JSON Pointer). It rides along with each turn and
is surfaced to the agent as a hint about what you're looking at. In the web UI the front end sends
it automatically; in the CLI you can emulate it with `/ptr <pointer>`, and it sticks to every
following turn until you change or clear it. It's a hint, not a command — the agent isn't forced
to act on it.

## Next

- The two tools in the context of the whole tool set: [tools.md](tools.md)
- How the gate and the parking work inside a turn: [runtime.md](runtime.md)
- The exact CLI commands and HTTP endpoints: [interfaces.md](interfaces.md)
