# kraken_nonce.py
import time
import fcntl
from pathlib import Path

NONCE_FILE = Path("/var/lib/kraken/kraken_nonce.txt")

def get_nonce():
    NONCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(NONCE_FILE, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        data = f.read().strip()
        last = int(data) if data else 0
        now = int(time.time() * 1000)
        nonce = max(now, last + 1)
        f.seek(0)
        f.truncate()
        f.write(str(nonce))
        f.flush()
        fcntl.flock(f, fcntl.LOCK_UN)
        return nonce
