import os
import re
import sqlite3
import sys
from pathlib import Path

from .errors import EXIT_SCHEMA, EXIT_SOURCE, NHError
from .dates import parse_key

DB_NAME = "notes.sqlite3"
BUSY_TIMEOUT = 5.0
ID_RE = re.compile(r"[0-9a-f]{64}")
REQUIRED = {
    "notes": ("date", "content", "updated_at"),
    "images": ("id", "checksum", "mime_type", "file_name", "data"),
}


def _platform_candidates(env):
    home = Path.home()
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / "NoteRepo"]
    if sys.platform == "win32":
        return [Path(env.get("APPDATA") or home / "AppData" / "Roaming") / "NoteRepo"]
    out = []
    if env.get("XDG_CONFIG_HOME"):
        out.append(Path(env["XDG_CONFIG_HOME"]) / "NoteRepo")
    out.append(home / ".config" / "NoteRepo")
    return out


def _db_file(p):
    p = Path(p).expanduser()
    if p.is_file():
        return p
    return p if p.suffix == ".sqlite3" else p / DB_NAME


def find_source(explicit=None, env=os.environ, env_precedence="notehatch_first"):
    """Path of the database to read. Explicit choices are never silently replaced."""
    if explicit:
        f = _db_file(explicit)
        if not f.is_file():
            raise NHError(EXIT_SOURCE, f"no NoteRepo database at {f} (from --source); "
                                       f"give a data folder or a {DB_NAME} file")
        return f
    tried = []
    order = ["NOTEHATCH_SOURCE", "NOTEREPO_DATA_DIR"]
    if env_precedence == "noterepo_first":
        order.reverse()
    for name in order:
        value = env.get(name)
        if not value:
            continue
        f = _db_file(value)
        if f.is_file():
            return f
        if name == "NOTEHATCH_SOURCE":
            raise NHError(EXIT_SOURCE, f"no NoteRepo database at {f} (from {name}); "
                                       f"give a data folder or a {DB_NAME} file")
        tried.append(f)
    for c in _platform_candidates(env):
        f = _db_file(c)
        tried.append(f)
        if f.is_file():
            return f
    lines = "\n".join(f"  {t}" for t in tried)
    raise NHError(EXIT_SOURCE, f"no NoteRepo database found. Paths tried:\n{lines}\n"
                               "Use --source <folder or notes.sqlite3> to point at it.")


def busy_error(path):
    return NHError(EXIT_SOURCE, f"NoteRepo is busy: {path} stayed locked for more than "
                                f"{BUSY_TIMEOUT:g} seconds. Retry in a moment.")


def translate(e, path):
    msg = str(e)
    if "locked" in msg or "busy" in msg:
        return busy_error(path)
    return NHError(EXIT_SOURCE, f"cannot read {path}: {msg}")


def _key_bytes(d):
    """A date key as bytes; NULL or non-text keys become text that never parses as a date."""
    if isinstance(d, bytes):
        return d
    return b"<null>" if d is None else str(d).encode("utf-8", "replace")


class Source:
    """Read-only, single-snapshot view of a NoteRepo 1.2.0 database."""

    def __init__(self, path):
        self.path = Path(path)
        self.conn = None

    def __enter__(self):
        p = self.path
        try:
            with open(p, "rb") as f:
                head = f.read(16)
        except OSError as e:
            raise NHError(EXIT_SOURCE, f"cannot read {p}: {e.strerror or e}")
        if head != b"SQLite format 3\x00":
            raise NHError(EXIT_SOURCE, f"{p} is not a SQLite database")
        try:
            self.conn = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True,
                                        timeout=BUSY_TIMEOUT, isolation_level=None)
            self.conn.text_factory = bytes
            self.conn.execute("PRAGMA query_only=ON")
            self.conn.execute("BEGIN")
            self._check_schema()
        except sqlite3.Error as e:
            self.close()
            raise translate(e, p)
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            finally:
                self.conn = None

    def _check_schema(self):
        for table, cols in REQUIRED.items():
            have = {r[1].decode() for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if not have:
                raise NHError(EXIT_SCHEMA, f"{self.path}: table '{table}' is missing. "
                                           "Only the NoteRepo 1.2.0 schema was studied.")
            for c in cols:
                if c not in have:
                    raise NHError(EXIT_SCHEMA, f"{self.path}: column '{table}.{c}' is missing. "
                                               "Only the NoteRepo 1.2.0 schema was studied.")

    # -- reads -----------------------------------------------------------
    def notes(self):
        """Yield (date_bytes, content_bytes, updated_at str or None) ordered by date."""
        for d, c, u in self.conn.execute("SELECT date, content, updated_at FROM notes ORDER BY date"):
            yield _key_bytes(d), (c if c is not None else b""), (u.decode("utf-8", "replace") if u is not None else None)

    def note(self, key):
        r = self.conn.execute("SELECT content, updated_at FROM notes WHERE date = ?", (key,)).fetchone()
        if r is None:
            return None
        return r[0] or b"", (r[1].decode("utf-8", "replace") if r[1] is not None else None)

    def dates(self):
        return [_key_bytes(r[0]).decode("utf-8", "replace") for r in self.conn.execute("SELECT date FROM notes")]

    def image_meta(self):
        """({id: (rowid, mime, file_name, size)}, [warnings]); no image bytes are read."""
        out, warns = {}, []
        for rowid, i, mime, fn, size in self.conn.execute(
                "SELECT rowid, id, mime_type, file_name, length(data) FROM images ORDER BY id"):
            i = i.decode("utf-8", "replace")
            if not ID_RE.fullmatch(i):
                warns.append(f"image id {i[:80]!r} is not 64 lowercase hex characters; ignored")
                continue
            out[i] = (rowid, mime.decode("utf-8", "replace").strip().lower(),
                      fn.decode("utf-8", "replace"), size or 0)
        return out, warns

    def blob_chunks(self, rowid, chunk=1 << 20):
        with self.conn.blobopen("images", "data", rowid, readonly=True) as b:
            while True:
                data = b.read(chunk)
                if not data:
                    return
                yield data
