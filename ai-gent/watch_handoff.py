#!/usr/bin/env python3
import time
from pathlib import Path
import shutil

INCOMING = Path.home() / "ai-control/handoff/incoming"
PROCESSING = Path.home() / "ai-control/handoff/processing"
APPLIED = Path.home() / "ai-control/handoff/applied"
FAILED = Path.home() / "ai-control/handoff/failed"

for d in [PROCESSING, APPLIED, FAILED]:
    d.mkdir(parents=True, exist_ok=True)

POLL_SECONDS = 5


def process_file(path: Path):
    """
    This is where existing AI-GENT logic goes:
    - parse YAML
    - validate against AI_RULES v2.0
    - apply change
    """
    print(f"Processing {path.name}")
    # placeholder
    return True


def main():
    print("AI-GENT directory watcher started")

    while True:
        files = sorted(INCOMING.glob("*.yaml"))
        for f in files:
            proc_path = PROCESSING / f.name
            shutil.move(f, proc_path)

            try:
                ok = process_file(proc_path)
                target = APPLIED if ok else FAILED
            except Exception as e:
                print(f"ERROR: {e}")
                target = FAILED

            shutil.move(proc_path, target / proc_path.name)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
