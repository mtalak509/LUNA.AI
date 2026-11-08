# Files

- [File tools and output truncation](file-tools.md) - The seven general-purpose file tools (read, write, edit, ls, find, grep, rm), their exact semantics and errors, and how large tool output is cut and spilled to .truncated_outputs/ so one call cannot flood the context.
- [The flat tool pool and per-agent slices](tool-pool.md) - How LUNA defines one shared pool of tools, tags each tool with declarative attributes, computes each agent's slice by subtraction, gates optional subsystems with feature flags, and gives tools access to process services.
