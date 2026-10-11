"""Fixture NoteRepo 1.2.0 databases built by the tests themselves."""
import hashlib
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE notes (date TEXT PRIMARY KEY, content TEXT NOT NULL DEFAULT '', updated_at TEXT) STRICT;
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;
CREATE TABLE images (id TEXT PRIMARY KEY, checksum TEXT NOT NULL UNIQUE, mime_type TEXT NOT NULL,
  file_name TEXT NOT NULL, width INTEGER, height INTEGER, byte_size INTEGER NOT NULL,
  data BLOB NOT NULL, created_at TEXT NOT NULL) STRICT;
"""


def make_db(folder, notes=(), images=(), wal=True, schema=SCHEMA):
    """notes: (date, content, updated_at); images: (mime, name, bytes[, id_override])."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(folder / "notes.sqlite3", isolation_level=None)
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(schema)
    ids = []
    for img in images:
        mime, name, data = img[:3]
        iid = img[3] if len(img) > 3 else hashlib.sha256(data).hexdigest()
        ids.append(iid)
        conn.execute("INSERT INTO images VALUES (?,?,?,?,1,1,?,?,?)",
                     (iid, iid, mime, name, len(data), data, "2026-01-01T00:00:00.000Z"))
    conn.executemany("INSERT INTO notes VALUES (?,?,?)", notes)
    return conn, ids, folder / "notes.sqlite3"
