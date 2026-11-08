# Files

- [CLI REPL](cli.md) - The luna command-line REPL — one fixed in-process session, how a turn is driven with concurrent answers to agent questions and cancellation, the slash commands, input and rendering.
- [HTTP server, API and SSE stream](http-api.md) - The FastAPI agent server — startup, the session-scoped route surface with status codes, the asynchronous turn model over one long-lived Server-Sent Events stream, and how events are normalized for the browser.
