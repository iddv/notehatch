import argparse
import json
import os
import sqlite3
import sys

from . import STUDIED_VERSION, __version__
from . import config as cfg
from . import demo as demo_mod
from . import export as exp
from . import verify as ver
from .dates import date_arg, local_display, parse_key, title
from .errors import (EXIT_INTERRUPT, EXIT_OK, EXIT_OUTPUT, EXIT_USAGE, EXIT_WARN, NHError)
from .render import ImageIndex, build_file, has_content, image_refs, line_count
from .source import Source, find_source, translate


def err(msg):
    print(msg, file=sys.stderr)


def warn(msg):
    print(f"warning: {msg}", file=sys.stderr)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def build_parser():
    p = Parser(prog="notehatch", description="Export NoteRepo daily notes to plain Markdown files "
               "(independent tool; reads NoteRepo's database read-only).")
    p.add_argument("--version", action="version",
                   version=f"notehatch {__version__} (studied NoteRepo {STUDIED_VERSION})")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command", parser_class=Parser)

    def common(sp, source=True, settings=True):
        if source:
            sp.add_argument("--source", help="NoteRepo data folder, notes.sqlite3 file or NoteRepo backup")
        if settings:
            sp.add_argument("--config", help="config file to use")
            sp.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                            help="override a setting for this run")

    def rng(sp):
        sp.add_argument("--from", dest="frm", type=date_arg, metavar="YYYY-MM-DD", help="first day to include")
        sp.add_argument("--to", type=date_arg, metavar="YYYY-MM-DD", help="last day to include")

    sp = sub.add_parser("locate", help="find the NoteRepo database and summarise it")
    common(sp)
    sp = sub.add_parser("demo", help="write a demo NoteRepo-format database into DIR")
    sp.add_argument("dir")
    sp = sub.add_parser("list", help="list days with content, newest first")
    common(sp)
    rng(sp)
    sp.add_argument("--json", action="store_true")
    sp = sub.add_parser("show", help="print the file an export would write for a day")
    common(sp)
    sp.add_argument("date", type=date_arg, metavar="YYYY-MM-DD")
    sp.add_argument("--json", action="store_true")
    sp = sub.add_parser("export", help="export notes to Markdown files in DIR")
    common(sp)
    rng(sp)
    sp.add_argument("dir")
    sp.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    sp.add_argument("--force", action="store_true", help="write into an unknown folder / different source")
    sp.add_argument("--rebuild-manifest", action="store_true",
                    help="re-hash existing files and treat them as edited-in-export")
    sp = sub.add_parser("verify", help="check an export folder against the source")
    common(sp)
    rng(sp)
    sp.add_argument("dir")
    sp.add_argument("--json", action="store_true")
    sp = sub.add_parser("config", help="show settings, or `config set KEY VALUE`")
    common(sp, source=False)
    sp.add_argument("args", nargs="*", metavar="set KEY VALUE")
    return p


def check_range(a):
    if getattr(a, "frm", None) and getattr(a, "to", None) and a.frm > a.to:
        raise NHError(EXIT_USAGE, f"--from {a.frm} is later than --to {a.to}; the range is empty")


def settings_of(a):
    return cfg.values(cfg.resolve(getattr(a, "config", None), getattr(a, "set", []), os.environ))


def open_source(a):
    mode = settings_of(a)["source.env_precedence"]
    return Source(find_source(getattr(a, "source", None), os.environ, mode))


# -- commands ------------------------------------------------------------
def cmd_locate(a):
    with open_source(a) as s:
        days = nulls = images = bad = 0
        empty = 0
        lo = hi = None
        for raw, content, upd in s.notes():
            key = raw.decode("utf-8", "replace")
            if not has_content(content):
                empty += 1
                continue
            if parse_key(key) is None:
                bad += 1
                continue
            days += 1
            nulls += upd is None
            lo, hi = (key if lo is None else min(lo, key)), (key if hi is None else max(hi, key))
        images = len(s.image_meta()[0])
        print(f"Source: {s.path}")
        print(f"Days with content: {days}")
        print(f"Empty rows (will be skipped): {empty}")
        print(f"Images: {images}")
        print(f"Date range: {lo} to {hi}" if lo else "Date range: none")
        print(f"Notes without updated_at: {nulls}")
        if bad:
            print(f"Rows with an invalid date key (will be skipped with a warning): {bad}")
    return EXIT_OK


def _days(s, a):
    """(key, date, content, updated) for valid days with content in range, newest first."""
    res = exp.Result()
    s_ = {"export.empty_days": "skip"}
    days = list(exp.iter_days(s, s_, a.frm, a.to, res))
    for w in res.warnings:
        warn(w)
    days.sort(key=lambda x: x[0], reverse=True)
    return days


def cmd_list(a):
    check_range(a)
    with open_source(a) as s:
        days = _days(s, a)
        rows = []
        for key, d, content, upd in days:
            rows.append({"date": key, "title": title(d), "updated_at": upd, "lines": line_count(content),
                         "images": len(image_refs(content))})
    if a.json:
        print(json.dumps({"total": len(rows), "notes": rows}, indent=2, ensure_ascii=False))
    else:
        for r in rows:
            print(f"{r['date']}  {r['title']:<28}  updated {local_display(r['updated_at'])}  "
                  f"{r['lines']} lines  {r['images']} images")
        print(f"{len(rows)} days with content")
    return EXIT_OK


def cmd_show(a):
    settings = settings_of(a)
    with open_source(a) as s:
        row = s.note(a.date)
        if row is None or not (has_content(row[0]) or settings["export.empty_days"] == "include"):
            err(f"no note for {a.date}")
            return EXIT_WARN
        content, upd = row
        meta, mw = s.image_meta()
        images = ImageIndex(meta)
        data, used, warns = build_file(a.date, parse_key(a.date), content, upd, settings, images)
    for w in mw + warns:
        warn(w)
    refs = [{"id": i, "file": f"assets/{images.name(i)}" if images.name(i) else None,
             "mime_type": meta[i][1] if i in meta else None,
             "file_name": meta[i][2] if i in meta else None} for i in dict.fromkeys(image_refs(content))]
    text = data.decode("utf-8", "replace")
    if a.json:
        print(json.dumps({"date": a.date, "title": title(parse_key(a.date)), "updated_at": upd,
                          "file": f"{a.date}.md", "text": text, "images": refs}, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(text)
        sys.stdout.flush()
        if refs:
            err(f"\nImages referenced ({len(refs)}):")
            for r in refs:
                err(f"  {r['id']} -> {r['file'] or 'not found in source (dangling)'}"
                    + (f"  [{r['mime_type']}, {r['file_name']}]" if r["mime_type"] else ""))
    return EXIT_OK


def cmd_export(a):
    check_range(a)
    settings = settings_of(a)
    with open_source(a) as s:
        r = exp.run(s, a.dir, settings, a.frm, a.to, a.dry_run, a.force, a.rebuild_manifest)
    if r.lock_notice:
        err(f"notice: {r.lock_notice}")
    for w in r.warnings:
        warn(w)
    if a.dry_run:
        for action, date, detail in r.plan:
            print(f"{action:<8} {date}  ({detail})")
    if r.in_scope == 0 and not r.interrupted:
        err("nothing to export")
        return EXIT_WARN
    verb = "Plan (dry run, nothing written)" if a.dry_run else "Export summary"
    print(f"{verb}:")
    print(f"  files written:      {r.written} (new {r.new}, changed {r.changed})" if not a.dry_run
          else f"  files to write:     {r.written} (new {r.new}, changed {r.changed})")
    print(f"  unchanged:          {r.unchanged}")
    print(f"  conflicts:          {len(r.conflicts)}")
    print(f"  skipped (bad date): {r.skipped_invalid}")
    print(f"  = days with content in scope: {r.in_scope}")
    print(f"  empty days skipped: {r.empty_skipped}")
    print(f"  images {'to save' if a.dry_run else 'saved'}:       {r.images_saved}")
    if a.dry_run:
        print(f"  total bytes to write: {r.bytes}")
    print(f"  gone from source:   {len(r.gone)}")
    print(f"  warnings:           {len(r.warnings)}")
    for date, why in r.conflicts:
        print(f"  conflict {date}: {why}; file kept (set export.edited_files=overwrite to replace)")
    for date in r.gone:
        print(f"  gone from source {date}: file kept")
    print(f"Output: {r.out}")
    print(f"Elapsed: {r.elapsed:.2f}s")
    if r.interrupted:
        err(f"interrupted: {r.written} files were written and recorded; run the export again to finish")
        return EXIT_INTERRUPT
    if r.warnings or r.conflicts:
        return EXIT_WARN
    if settings["export.unchanged_exit"] == "warn" and r.written == 0 and not a.dry_run:
        return EXIT_WARN
    return EXIT_OK


def cmd_verify(a):
    check_range(a)
    settings = settings_of(a)
    with open_source(a) as s:
        rep = ver.run(s, a.dir, settings, a.frm, a.to)
    for w in rep["warnings"]:
        warn(w)
    if a.json:
        print(json.dumps(rep, indent=2, ensure_ascii=False))
    else:
        print(f"Verified: {rep['verified']}")
        for label, key in (("Missing", "missing"), ("Out of date", "out_of_date"), ("Edited", "edited"),
                           ("Orphaned (not in source)", "orphaned")):
            print(f"{label}: {len(rep[key])}")
            for x in rep[key]:
                print(f"  {x}")
        print(f"Bad images: {len(rep['bad_images'])}")
        for b in rep["bad_images"]:
            print(f"  {b['id']} ({b['file']}): {b['problem']}")
        if rep["problems"]:
            print(f"PROBLEMS: {len(rep['missing'])} missing, {len(rep['out_of_date'])} out of date, "
                  f"{len(rep['edited'])} edited, {len(rep['bad_images'])} bad images")
        else:
            print(f"OK: {rep['verified']} notes, {rep['images_checked']} images match source")
    return EXIT_WARN if rep["problems"] else EXIT_OK


def cmd_config(a):
    args = a.args
    if not args:
        for key, (value, src) in cfg.resolve(a.config, a.set, os.environ).items():
            print(f"{key} = {value}   ({src})")
        return EXIT_OK
    if args[0] == "set" and len(args) == 3:
        path = cfg.set_value(args[1], args[2], a.config)
        print(f"{args[1]} = {args[2]}  (saved to {path})")
        return EXIT_OK
    raise NHError(EXIT_USAGE, "usage: notehatch config [set KEY VALUE]")


def cmd_demo(a):
    path = demo_mod.build(a.dir)
    print(f"Wrote demo database {path}\nTry: notehatch locate --source {a.dir}")
    return EXIT_OK


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return globals()[f"cmd_{args.cmd}"](args)
    except NHError as e:
        err(f"notehatch: {e.message}")
        return e.code
    except sqlite3.Error as e:
        path = getattr(args, "source", None) or "the NoteRepo database"
        ne = translate(e, path)
        err(f"notehatch: {ne.message}")
        return ne.code
    except KeyboardInterrupt:
        err("interrupted")
        return EXIT_INTERRUPT
    except BrokenPipeError:
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
