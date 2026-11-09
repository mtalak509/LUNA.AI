from pathlib import Path
from threading import Event

Path("ready.txt").write_text("ready\n", encoding="utf-8")
print("READY", flush=True)
Event().wait()
