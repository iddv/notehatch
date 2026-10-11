import json
from pathlib import Path

from . import __version__
from .errors import EXIT_OUTPUT, NHError
from .fsutil import atomic_write

FORMAT = 1
DIRNAME = ".notehatch"


def manifest_path(out):
    return Path(out) / DIRNAME / "manifest.json"


def new(source):
    return {"format": FORMAT, "tool_version": __version__, "source": str(source),
            "settings": {}, "files": {}, "images": {}}


def _upgrade(m):
    """Bring an older manifest layout up to FORMAT (format 1 is the first)."""
    m.setdefault("settings", {})
    m.setdefault("images", {})
    m["format"] = FORMAT
    return m


def load(out):
    """The manifest dict, or None when the folder has none. Corrupt -> NHError."""
    p = manifest_path(out)
    if not p.exists():
        return None
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(m, dict) or not isinstance(m.get("files"), dict):
            raise ValueError("missing 'files' table")
    except (OSError, ValueError) as e:
        raise NHError(EXIT_OUTPUT, f"manifest {p} is corrupt ({e}); run the export again with "
                                   "--rebuild-manifest to re-hash existing files")
    fmt = m.get("format", 0)
    if not isinstance(fmt, int) or fmt > FORMAT:
        raise NHError(EXIT_OUTPUT, f"manifest {p} has format {fmt!r}, newer than this NoteHatch understands ({FORMAT})")
    return _upgrade(m) if fmt < FORMAT else m


def save(out, m):
    m["tool_version"] = __version__
    atomic_write(manifest_path(out), (json.dumps(m, indent=1, sort_keys=True) + "\n").encode("utf-8"))
