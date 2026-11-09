# Files

- [File tools and output truncation](file-tools.md) - The seven general-purpose file tools (read, write, edit, ls, find, grep, rm), their exact semantics and errors, and how large tool output is cut and spilled to .truncated_outputs/ so one call cannot flood the context.
- [Host shell and session tasks](host-shell.md) - How LUNA runs Bash or PowerShell commands with host-user access, shares foreground and background tasks across agents, pages bounded output and cleans up owned processes when a session closes.
- [The flat tool pool and per-agent slices](tool-pool.md) - How LUNA defines one shared pool of tools, tags each tool with declarative attributes, computes each agent's slice by subtraction, gates optional subsystems with feature flags, and gives tools access to process services.
