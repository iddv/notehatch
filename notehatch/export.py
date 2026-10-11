"""Export planning and writing; also used by `show` and `verify`."""
import datetime as dt
import os
import signal
import time
from pathlib import Path

from . import STUDIED_VERSION
from .dates import noon_local_ns, parse_key, utc_to_ns
from .errors import EXIT_OUTPUT, EXIT_USAGE, NHError
from .fsutil import Lock, atomic_write, clean_temp_files, hash_file, make_dir
from . import config as cfg
from . import manifest as mf
from .render import ImageIndex, build_file, has_content, sha256

_after_write = None  # test hook: called after each note file lands


class Result:
    def __init__(self):
        self.in_scope = 0
        self.new = self.changed = self.unchanged = self.skipped_invalid = self.empty_skipped = 0
        self.conflicts = []   # (date, reason)
        self.gone = []
        self.warnings = []
        self.plan = []        # (action, date, detail) for dry runs
        self.images_saved = 0
        self.bytes = 0
        self.interrupted = False
        self.lock_notice = None

    @property
    def written(self):
        return self.new + self.changed


def in_range(key, frm, to):
    return (frm is None or key >= frm) and (to is None or key <= to)


def iter_days(src, settings, frm, to, result):
    """Yield (key, date, content, updated) for each in-scope valid day; account for the rest."""
    include = settings["export.empty_days"] == "include"
    ranged = frm is not None or to is not None
    for raw_key, content, updated in src.notes():
        text = raw_key.decode("utf-8", "replace")
        d = parse_key(text) if raw_key.isascii() else None
        full = has_content(content)
        if d is not None and not in_range(text, frm, to):
            continue
        if d is None and ranged:
            continue
        if not full and not include:
            result.empty_skipped += 1
            continue
        result.in_scope += 1
        if d is None:
            result.skipped_invalid += 1
            result.warnings.append(f"{text[:40]!r}: invalid date key; note skipped")
            continue
        yield text, d, content, updated


def mtime_for(settings, date, d, updated, started_ns, result):
    if settings["export.mtime"] == "export_time":
        return started_ns
    if updated is None:
        return noon_local_ns(d)
    ns = utc_to_ns(updated)
    if ns is None:
        result.warnings.append(f"{date}: updated_at {updated!r} is not a valid UTC timestamp; using 12:00 local")
        return noon_local_ns(d)
    return ns


class _Term(Exception):
    pass


def run(src, out, settings, frm=None, to=None, dry_run=False, force=False, rebuild=False):
    started = time.monotonic()
    started_ns = time.time_ns()
    res = Result()
    out = Path(out).expanduser()
    if out.exists() and not out.is_dir():
        raise NHError(EXIT_OUTPUT, f"output path {out} exists and is not a folder")
    _refuse_source_folder(out, src.path)
    meta, mwarn = src.image_meta()
    images = ImageIndex(meta)
    res.warnings += mwarn

    m = None
    if out.is_dir():
        if rebuild:
            m = _rebuild(out, src.path)
        else:
            m = mf.load(out)
    if m is not None and not rebuild and m.get("source") not in (None, str(src.path)) and not force:
        raise NHError(EXIT_USAGE, f"this export folder came from a different source.\n  manifest: {m.get('source')}\n"
                                  f"  current:  {src.path}\nUse --force to continue with the current source.")
    had_manifest = m is not None
    if m is None:
        m = mf.new(src.path)
        _check_collisions(out, src, images, force, settings)
    m["source"] = str(src.path)

    lock = None
    old_term = None
    if not dry_run:
        make_dir(out)
        make_dir(out / mf.DIRNAME)
        lock = Lock(out / mf.DIRNAME / "lock", settings["lock.stale_requires_age"] == "true").acquire()
        res.lock_notice = lock.notice
        try:
            clean_temp_files(out, out / "assets")
            if settings["export.images"] == "extract":
                make_dir(out / "assets")
            probe = out / mf.DIRNAME / ".write-test"
            atomic_write(probe, b"")
            probe.unlink()
        except BaseException:
            lock.release()
            raise

        def _on_term(signum, frame):
            raise KeyboardInterrupt
        try:
            old_term = signal.signal(signal.SIGTERM, _on_term)
        except (ValueError, OSError):
            old_term = None

    m["settings"] = {k: settings[k] for k in cfg.ORIGINAL}
    seen = set()
    handled_images = {}
    last_flush = 0.0

    def flush(force_=False):
        nonlocal last_flush
        if dry_run:
            return
        now = time.monotonic()
        if force_ or len(m["files"]) < 300 or now - last_flush > 0.25:
            mf.save(out, m)
            last_flush = now

    try:
        for date, d, content, updated in iter_days(src, settings, frm, to, res):
            seen.add(date)
            target, used, warns = build_file(date, d, content, updated, settings, images)
            res.warnings += warns
            th, ch = sha256(target), sha256(content)
            path = out / f"{date}.md"
            entry = m["files"].get(date)
            disk = _file_hash(path)
            overwrite = settings["export.edited_files"] == "overwrite"
            action = None
            if disk == th:
                stale_mtime = (entry is not None and settings["export.mtime_change_rewrites"] == "true"
                               and entry.get("mtime_mode", "updated") != settings["export.mtime"])
                if entry is None or (entry.get("content_sha256") == ch and entry.get("updated_at") == updated
                                     and not stale_mtime):
                    action = "unchanged"
                    if entry is None:
                        m["files"][date] = _entry(date, updated, ch, th, settings["export.mtime"])
                else:
                    action = "changed"
            elif entry is None:
                action = "new" if disk is None else ("changed" if (force or overwrite) else "conflict")
                reason = "file exists but is not from this export"
            elif disk is None:
                if settings["export.missing_files"] == "conflict":
                    action, reason = "conflict", "deleted from export"
                else:
                    action = "new"
            elif disk != entry.get("file_sha256") and not overwrite:
                action, reason = "conflict", "edited in export"
            else:
                action = "changed"
            if action == "conflict":
                res.conflicts.append((date, reason))
                res.plan.append(("conflict", date, reason))
                continue
            if action == "unchanged":
                res.unchanged += 1
                res.plan.append(("skip", date, "unchanged"))
                if settings["export.images"] == "extract":
                    _images(src, out, used, images, handled_images, res, m, dry_run, force or overwrite)
                continue
            res.plan.append(("write", date, action))
            res.bytes += len(target)
            if settings["export.images"] == "extract":
                _images(src, out, used, images, handled_images, res, m, dry_run, force or overwrite)
            if not dry_run:
                atomic_write(path, target, mtime_for(settings, date, d, updated, started_ns, res))
                m["files"][date] = _entry(date, updated, ch, th, settings["export.mtime"])
                flush()
            if action == "new":
                res.new += 1
            else:
                res.changed += 1
            if _after_write and not dry_run:
                _after_write(res)
        for date in sorted(m["files"]):
            if date not in seen and in_range(date, frm, to):
                res.gone.append(date)
    except KeyboardInterrupt:
        res.interrupted = True
    finally:
        try:
            if lock is not None:
                flush(True)
        finally:
            if lock is not None:
                lock.release()
            if old_term is not None:
                signal.signal(signal.SIGTERM, old_term)
    res.elapsed = time.monotonic() - started
    res.out = out
    return res


def _entry(date, updated, ch, th, mtime_mode):
    return {"date": date, "mtime_mode": mtime_mode, "updated_at": updated, "content_sha256": ch, "file_sha256": th,
            "exported_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"}


def _file_hash(path):
    try:
        return hash_file(path)
    except FileNotFoundError:
        return None
    except IsADirectoryError:
        raise NHError(EXIT_OUTPUT, f"{path} is a folder; cannot write the note file there")
    except OSError as e:
        raise NHError(EXIT_OUTPUT, f"cannot read {path}: {e.strerror or e}")


def _blob_hash(src, rowid):
    import hashlib
    h = hashlib.sha256()
    for c in src.blob_chunks(rowid):
        h.update(c)
    return h.hexdigest()


def _images(src, out, used, images, handled, res, m, dry_run, replace):
    for iid in used:
        if iid in handled:
            continue
        handled[iid] = True
        rowid, mime, fname, size = images.meta[iid]
        name = images.name(iid)
        path = out / "assets" / name
        if not images.known_mime(iid):
            res.warnings.append(f"image {iid[:16]}: unknown MIME type {mime!r}; saved as .bin")
        digest = _blob_hash(src, rowid)
        if digest != iid:
            res.warnings.append(f"image {iid[:16]}: stored bytes hash to {digest[:16]}..., not the id; written anyway")
        existing = _file_hash(path) if path.exists() else None
        if existing == digest:
            m["images"][iid] = {"file": f"assets/{name}"}
            continue
        if existing is not None and not replace:
            res.warnings.append(f"assets/{name}: differs from the source image; left alone "
                                "(set export.edited_files=overwrite to replace it)")
            continue
        res.images_saved += 1
        res.bytes += size
        if not dry_run:
            atomic_write(path, src.blob_chunks(rowid))
            m["images"][iid] = {"file": f"assets/{name}"}


def _check_collisions(out, src, images, force, settings):
    if force or not out.is_dir():
        return
    present = [p for p in out.iterdir() if p.name != mf.DIRNAME]
    if not present:
        return
    if settings["export.unknown_folder"] == "refuse_any_files":
        raise NHError(EXIT_OUTPUT, f"{out} has no NoteHatch manifest and already holds files "
                                   f"({', '.join(sorted(p.name for p in present)[:5])}). "
                                   "Use --force to write into it anyway.")
    hits = []
    for key in src.dates():
        if (out / f"{key}.md").exists():
            hits.append(f"{key}.md")
    for name in images.names.values():
        if (out / "assets" / name).exists():
            hits.append(f"assets/{name}")
    if hits:
        shown = ", ".join(hits[:5]) + (f" and {len(hits) - 5} more" if len(hits) > 5 else "")
        raise NHError(EXIT_OUTPUT, f"{out} has no NoteHatch manifest and already holds files that would be "
                                   f"overwritten ({shown}). Use --force to write into it anyway.")


def _rebuild(out, source):
    """Fresh manifest in which every existing note file counts as edited-in-export."""
    m = mf.new(source)
    for p in sorted(out.glob("*.md")):
        if parse_key(p.stem) is not None:
            m["files"][p.stem] = {"date": p.stem, "updated_at": None, "content_sha256": "",
                                  "file_sha256": "", "exported_at": None}
    return m


def _refuse_source_folder(out, source):
    folder = Path(source).resolve().parent
    target = out.resolve()
    if target == folder or folder in target.parents:
        raise NHError(EXIT_OUTPUT, f"output folder {out} is the NoteRepo data folder {folder} "
                                   "(or inside it); NoteHatch never writes there. Choose another folder.")
