# Files

- [Checkpoints and undo](checkpoints-and-undo.md) - How LUNA's undo works as full copies of the workspace folder — when snapshots are taken, how numbering stays monotonic across restores, and how history is reloaded after a restore.
- [Sessions, process bootstrap and on-disk layout](sessions-and-storage.md) - Where LUNA keeps state on disk, how a process and a session are set up, how SessionManager and the AgentSession runner manage turns, and what survives a restart.
- [Filesystem zones, PathScope and attachments](zones-and-path-scope.md) - How LUNA isolates agents by resolving every file path through PathScope — who may read and write where, the hidden service folders — and how WorkspaceManager applies the same policy to user uploads in attachments/.
