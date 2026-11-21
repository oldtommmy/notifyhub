from contextlib import contextmanager
import json
import os
from pathlib import Path


class StateStore:
    def __init__(self, path, limit=1000):
        self.path = Path(path).expanduser()
        self.limit = limit
        self._seen = []
        self._set = set()
        self.load()

    def load(self):
        try:
            with self.path.open(encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return
        seen = data.get("seen", [])
        if isinstance(seen, list):
            self._seen = [str(item) for item in seen][-self.limit :]
            self._set = set(self._seen)

    def is_seen(self, key):
        return bool(key) and key in self._set

    def mark(self, key):
        if not key or key in self._set:
            return
        self._seen.append(key)
        self._set.add(key)
        if len(self._seen) > self.limit:
            removed = self._seen[: -self.limit]
            self._seen = self._seen[-self.limit :]
            self._set.difference_update(removed)

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(self.path.name + ".tmp")
            temp.write_text(json.dumps({"seen": self._seen}, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError:
            return


@contextmanager
def state_lock(path):
    """Serialize read/check/send/save across hook processes and threads."""
    lock_path = Path(str(path) + ".lock").expanduser()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            import time
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
