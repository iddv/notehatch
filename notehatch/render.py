"""Turning a note row into the bytes of its Markdown file."""
import hashlib
import re

from . import STUDIED_VERSION
from .dates import title

EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
       "image/webp": ".webp", "image/svg+xml": ".svg"}
_MARKER = re.compile(r"-|[0-9]+\.")
_IMG_REF = re.compile(rb"!\[([^\]\n]*)\]\(noterepo:image:([^)\s]*)\)")
_ID = re.compile(rb"[0-9a-f]{64}")
_PLAIN_TS = re.compile(r"[0-9T:.\-]+Z")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def has_content(content):
    text = content.decode("utf-8", "replace")
    for line in re.split(r"\r\n|\n|\r", text):
        s = line.strip()
        if s and not _MARKER.fullmatch(s):
            return True
    return False


def line_count(content):
    if not content:
        return 0
    parts = re.split(r"\r\n|\n|\r", content.decode("utf-8", "replace"))
    return len(parts) - (1 if parts[-1] == "" else 0)


def image_refs(content):
    """Ids (str) of every image reference in order of appearance, valid format or not."""
    return [m.group(2).decode("utf-8", "replace") for m in _IMG_REF.finditer(content)]


class ImageIndex:
    """File names for images: assets/<16 hex>.<ext>, the full id when prefixes collide."""

    def __init__(self, meta):
        self.meta = meta
        counts = {}
        for i in meta:
            counts[i[:16]] = counts.get(i[:16], 0) + 1
        self.names = {}
        for i, (_, mime, _, _) in meta.items():
            stem = i if counts[i[:16]] > 1 else i[:16]
            self.names[i] = stem + EXT.get(mime, ".bin")

    def name(self, image_id):
        return self.names.get(image_id)

    def known_mime(self, image_id):
        return self.meta[image_id][1] in EXT


def front_matter(date, d, updated):
    lines = ["---", f"date: {date}", f'title: "{title(d)}"']
    if updated is not None:
        lines.append(f"updated: {updated if _PLAIN_TS.fullmatch(updated) else _quote(updated)}")
    lines.append(f"source: NoteRepo {STUDIED_VERSION}")
    return ("\n".join(lines) + "\n---\n\n").encode("utf-8")


def _quote(s):
    import json
    return json.dumps(s, ensure_ascii=False)


def build_file(date, d, content, updated, settings, images):
    """(file bytes, [image ids used], [warnings]) for one day."""
    warns, used = [], []
    body = content if has_content(content) else b""  # empty_days=include writes an empty body
    if settings["export.images"] == "extract":
        def sub(m):
            raw = m.group(2)
            iid = raw.decode("utf-8", "replace")
            name = images.name(iid) if _ID.fullmatch(raw) else None
            if name is None:
                warns.append(f"{date}: dangling image reference {iid!r} left as is")
                return m.group(0)
            if iid not in used:
                used.append(iid)
            return b"![" + m.group(1) + b"](assets/" + name.encode() + b")"
        body = _IMG_REF.sub(sub, body)
    head = front_matter(date, d, updated) if settings["export.front_matter"] == "yaml" else b""
    return head + body, used, warns
