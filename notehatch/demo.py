"""Builds a realistic NoteRepo 1.2.0 database for trying the tool."""
import base64
import datetime as dt
import hashlib
import random
import sqlite3
import struct
import zlib
from pathlib import Path

from .errors import EXIT_OUTPUT, EXIT_USAGE, NHError
from .source import DB_NAME

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (date TEXT PRIMARY KEY, content TEXT NOT NULL DEFAULT '', updated_at TEXT) STRICT;
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;
CREATE TABLE IF NOT EXISTS images (
  id TEXT PRIMARY KEY, checksum TEXT NOT NULL UNIQUE, mime_type TEXT NOT NULL, file_name TEXT NOT NULL,
  width INTEGER, height INTEGER, byte_size INTEGER NOT NULL, data BLOB NOT NULL, created_at TEXT NOT NULL
) STRICT;
"""

START = dt.date(2026, 7, 14)
DAYS = 90


def _png():
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    raw = b"".join(b"\x00" + b"".join(bytes((x * 30 % 256, y * 30 % 256, 160)) for x in range(8)) for y in range(8))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _images():
    jpeg = base64.b64decode("/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=")
    gif = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
    webp = base64.b64decode("UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4HAA==")
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><circle cx="16" cy="16" r="12" fill="#c0392b"/></svg>'
    raw = b"".join(b"\x00" + b"\xf0\xc0\x40" * 8 for _ in range(8))
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    png2 = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return [("image/png", "diagram.png", _png()), ("image/jpeg", "photo.jpg", jpeg),
            ("image/gif", "pixel.gif", gif), ("image/webp", "sticker.webp", webp),
            ("image/svg+xml", "logo.svg", svg), ("image/png", "diagram.png", png2)]


TOPICS = ["Project Atlas", "Reading list", "Garden", "Budget review", "Trip planning", "Team sync",
          "Recipes", "Bug triage", "Workout", "Ideas", "Café notes", "日本語の勉強"]
LINES = [
    "- Draft the outline for the next milestone",
    "- Call the dentist about Thursday",
    "- Read [The Pragmatic Programmer](https://pragprog.com/titles/tpp20/) chapter 4",
    "- Saved for later: https://example.com/articles/local-first-software",
    "- Interesting thread https://x.com/example/status/1780000000000000001",
    "- Résumé update: añadir proyectos, 履歴書を更新する 📝",
    "- Buy: oat milk, naïve pasta, crème fraîche",
    "- Follow up with Zoë about the invoice",
]


def _content(rng, day_index, images):
    topic = rng.choice(TOPICS)
    out = [f"- {topic}"]
    for _ in range(rng.randint(2, 5)):
        out.append("  " + rng.choice(LINES))
        if rng.random() < 0.4:
            out.append("    " + rng.choice(LINES))
        if rng.random() < 0.2:
            out.append("\t\t" + rng.choice(LINES))
    out.append("1. First thing")
    out.append("2. Second thing")
    out.append("  1. Nested numbered item")
    return "\n".join(out)


def build(target):
    target = Path(target).expanduser()
    db_path = target / DB_NAME
    if db_path.exists():
        raise NHError(EXIT_USAGE, f"{db_path} already exists; refusing to overwrite it")
    try:
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        conn = sqlite3.connect(db_path)
    except (OSError, sqlite3.Error) as e:
        raise NHError(EXIT_OUTPUT, f"cannot create {db_path}: {getattr(e, 'strerror', None) or e}")
    rng = random.Random(1202)
    imgs = _images()
    ids = []
    with conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        for mime, name, data in imgs:
            iid = hashlib.sha256(data).hexdigest()
            ids.append(iid)
            conn.execute("INSERT INTO images VALUES (?,?,?,?,?,?,?,?,?)",
                         (iid, iid, mime, name, 8, 8, len(data), data, "2026-07-14T09:00:00.000Z"))
        all_days = [START + dt.timedelta(days=i) for i in range(DAYS)]
        special = {dt.date(2026, 10, 7)}
        pool = [d for d in all_days if d not in special]
        rng.shuffle(pool)
        no_row, empty, content_days = pool[:14], pool[14:20], set(pool[20:]) | special
        assert len(content_days) == 70
        order = sorted(content_days)
        null_upd = set(rng.sample(order, 5))
        with_img = {order[3]: [0], order[10]: [1], order[18]: [2, 0], order[25]: [3], order[40]: [4], order[52]: [5],
                    dt.date(2026, 10, 7): [0, 4]}
        big = order[30]
        empties = ["", "   \n\n  ", "-", "1.", "- \n-\n  - ", "\t\n"]
        rows = []
        for i, d in enumerate(order):
            text = _content(rng, i, ids)
            for n in with_img.get(d, []):
                text += f"\n  - ![{imgs[n][1]}](noterepo:image:{ids[n]})"
            if d == big:
                text += "\n" + "\n".join(f"  - Long note line {k}: {'lorem ipsum dolor sit amet ' * 4}" for k in range(900))
            if d == order[60]:
                text += "\n  - ![gone](noterepo:image:" + hashlib.sha256(b"no such image").hexdigest() + ")"
            upd = None if d in null_upd else f"{d.isoformat()}T{rng.randint(6, 21):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}.{rng.randint(0, 999):03d}Z"
            rows.append((d.isoformat(), text, upd))
        for d, text in zip(empty, empties):
            rows.append((d.isoformat(), text, f"{d.isoformat()}T08:00:00.000Z"))
        rows.append(("2026-13-45", "- A note stored under a malformed date key", "2026-09-01T10:00:00.000Z"))
        conn.executemany("INSERT INTO notes VALUES (?,?,?)", rows)
        conn.execute("INSERT INTO metadata VALUES ('schema_version','1')")
    conn.close()
    try:
        db_path.chmod(0o600)
    except OSError:
        pass
    return db_path
