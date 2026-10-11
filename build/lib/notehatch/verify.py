from pathlib import Path

from . import config as cfg
from . import manifest as mf
from .errors import EXIT_USAGE, NHError
from .export import Result, in_range, iter_days
from .fsutil import hash_file
from .dates import parse_key
from .render import ImageIndex, build_file, sha256
from .export import _blob_hash


def run(src, out, settings, frm=None, to=None):
    out = Path(out).expanduser()
    m = mf.load(out) if out.is_dir() else None
    if m is None:
        raise NHError(EXIT_USAGE, f"{out} is not a NoteHatch export (no {mf.DIRNAME}/manifest.json)")
    # judge the folder by the settings it was written with
    used_settings = dict(settings)
    used_settings.update({k: v for k, v in m.get("settings", {}).items() if k in cfg.ORIGINAL})
    meta, mwarn = src.image_meta()
    images = ImageIndex(meta)
    tmp = Result()
    rep = {"verified": 0, "missing": [], "out_of_date": [], "edited": [], "orphaned": [],
           "bad_images": [], "images_checked": 0, "warnings": mwarn}
    expected, checked, needed = set(), set(), []
    for date, d, content, updated in iter_days(src, used_settings, frm, to, tmp):
        expected.add(date)
        if settings["verify.scope"] == "manifest_days" and date not in m["files"]:
            continue
        checked.add(date)
        target, used, warns = build_file(date, d, content, updated, used_settings, images)
        rep["warnings"] += warns
        for i in used:
            if i not in needed:
                needed.append(i)
        path = out / f"{date}.md"
        if not path.is_file():
            rep["missing"].append(date)
            continue
        disk = hash_file(path)
        entry = m["files"].get(date)
        if disk == sha256(target):
            rep["verified"] += 1
        elif entry is None or disk != entry.get("file_sha256"):
            rep["edited"].append(date)
        else:
            rep["out_of_date"].append(date)
    rep["warnings"] += tmp.warnings
    for i in needed:
        rep["images_checked"] += 1
        p = out / "assets" / images.name(i)
        if not p.is_file():
            rep["bad_images"].append({"id": i, "file": f"assets/{images.name(i)}", "problem": "missing"})
        elif hash_file(p) != i:
            rep["bad_images"].append({"id": i, "file": f"assets/{images.name(i)}", "problem": "hash differs from id"})
    for p in sorted(out.iterdir()):
        if p.name.startswith(".") or p.name == "assets":
            continue
        key = p.stem if p.suffix == ".md" else None
        if key is not None and parse_key(key) is not None:
            if key in expected or not in_range(key, frm, to):
                continue
        elif frm is not None or to is not None:
            continue  # unrelated files are not judged when only a range is verified
        rep["orphaned"].append(p.name)
    assets = out / "assets"
    if assets.is_dir() and used_settings["export.images"] == "extract":
        keep = {images.name(i) for i in needed}
        for p in sorted(assets.iterdir()):
            if p.name not in keep and not p.name.startswith("."):
                rep["orphaned"].append(f"assets/{p.name}")
    rep["problems"] = bool(rep["missing"] or rep["out_of_date"] or rep["edited"] or rep["bad_images"])
    return rep
