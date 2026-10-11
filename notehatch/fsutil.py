import hashlib
import json
import os
import sys
import time
from pathlib import Path

from .errors import EXIT_OUTPUT, NHError

TMP_PREFIX = ".notehatch-tmp-"


def hash_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def out_error(path, e):
    return NHError(EXIT_OUTPUT, f"cannot write {path}: {getattr(e, 'strerror', None) or e}")


def atomic_write(path, chunks, mtime_ns=None):
    """Write bytes (or an iterable of byte chunks) via a temp file in the same folder."""
    path = Path(path)
    if isinstance(chunks, (bytes, bytearray)):
        chunks = [chunks]
    tmp = path.with_name(f"{TMP_PREFIX}{os.getpid()}-{path.name}")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as f:
            for c in chunks:
                f.write(c)
            f.flush()
            os.fsync(f.fileno())
        if mtime_ns is not None:
            os.utime(tmp, ns=(mtime_ns, mtime_ns))
        os.replace(tmp, path)
    except OSError as e:
        raise out_error(path, e)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def make_dir(path):
    try:
        Path(path).mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as e:
        raise out_error(path, e)


def clean_temp_files(*dirs):
    for d in dirs:
        try:
            for p in Path(d).iterdir():
                if p.name.startswith(TMP_PREFIX):
                    p.unlink()
        except OSError:
            pass


def pid_running(pid):
    if pid <= 0 or pid > 2**31 - 1:
        return False
    if sys.platform == "win32":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


class Lock:
    STALE_SECONDS = 3600

    def __init__(self, path, requires_age=True):
        self.path = Path(path)
        self.requires_age = requires_age
        self.held = False
        self.notice = None

    def acquire(self):
        for attempt in range(2):
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                if attempt == 0 and self._stale():
                    continue
                raise NHError(EXIT_OUTPUT, f"another NoteHatch run holds the lock {self.path}; "
                                           "wait for it to finish (or remove the file if no run is active)")
            except OSError as e:
                raise out_error(self.path, e)
            with os.fdopen(fd, "w") as f:
                json.dump({"pid": os.getpid(), "time": time.time()}, f)
            self.held = True
            return self

    def _stale(self):
        try:
            age = time.time() - self.path.stat().st_mtime
            pid = json.loads(self.path.read_text()).get("pid")
        except (OSError, ValueError, AttributeError):
            pid, age = None, (time.time() - self.path.stat().st_mtime if self.path.exists() else 0)
        if isinstance(pid, int) and pid_running(pid):
            return False
        if age < self.STALE_SECONDS and (self.requires_age or not isinstance(pid, int)):
            return False
        try:
            os.unlink(self.path)
        except OSError:
            return False
        self.notice = (f"removed stale lock {self.path} (owner not running"
                       + (", older than 1 hour)" if age >= self.STALE_SECONDS else ")"))
        return True

    def release(self):
        if self.held:
            try:
                os.unlink(self.path)
            except OSError:
                pass
            self.held = False
