import contextlib
import datetime as dt
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from notehatch import cli, export as exp, source as srcmod
from notehatch.dates import ordinal, parse_key, title
from notehatch.render import has_content

from .fixtures import make_db

PNG = b"\x89PNG\r\n\x1a\nfake-png"
IMGS = [("image/png", "a.png", PNG), ("image/jpeg", "b.jpg", b"\xff\xd8jpeg"), ("image/gif", "c.gif", b"GIF89a"),
        ("image/webp", "d.webp", b"RIFFwebp"), ("image/svg+xml", "e.svg", b"<svg/>"), ("image/x-weird", "f.xyz", b"???")]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        env = {"HOME": str(self.tmp / "home"), "XDG_CONFIG_HOME": str(self.tmp / "xdg"),
               "NOTEHATCH_CONFIG": str(self.tmp / "cfg.toml")}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        for k in list(os.environ):
            if k.startswith("NOTEHATCH_") and k != "NOTEHATCH_CONFIG" or k == "NOTEREPO_DATA_DIR":
                del os.environ[k]
        self.src = self.tmp / "src"
        self.out = self.tmp / "out"

    def run_cli(self, *argv):
        o, e = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            try:
                rc = cli.main(list(argv))
            except SystemExit as x:
                rc = x.code
        return rc, o.getvalue(), e.getvalue()

    def db(self, notes=(), images=(), **kw):
        conn, ids, path = make_db(self.src, notes, images, **kw)
        self.conn = conn
        self.addCleanup(conn.close)
        return ids

    def export(self, *extra, out=None):
        return self.run_cli("export", str(out or self.out), "--source", str(self.src), *extra)


class TitleTests(unittest.TestCase):
    def test_ordinals(self):
        got = [ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 31)]
        self.assertEqual(got, ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "31st"])

    def test_title(self):
        self.assertEqual(title(dt.date(2026, 10, 7)), "Wed, October 7th, 2026")

    def test_keys(self):
        self.assertIsNone(parse_key("2026-02-30"))
        self.assertIsNone(parse_key("2026-2-3"))
        self.assertIsNone(parse_key("2026-10-07 "))
        self.assertIsNotNone(parse_key("2024-02-29"))

    def test_has_content(self):
        for empty in ("", "  \n\t", "-", "1.", "- \n 12. \n  -  "):
            self.assertFalse(has_content(empty.encode()), repr(empty))
        for full in ("- a", "-a", "1.5", "- \n- x"):
            self.assertTrue(has_content(full.encode()), repr(full))


class SourceTests(Base):
    def test_missing_source_lists_paths(self):
        rc, _, err = self.run_cli("locate")
        self.assertEqual(rc, 3)
        self.assertIn("Paths tried", err)
        self.assertIn(".config/NoteRepo", err)

    def test_explicit_missing(self):
        rc, _, err = self.run_cli("locate", "--source", str(self.tmp / "nope"))
        self.assertEqual(rc, 3)

    def test_env_dir_and_backup_file(self):
        self.db([("2026-01-01", "- x", None)])
        self.conn.close()
        with mock.patch.dict(os.environ, {"NOTEREPO_DATA_DIR": str(self.src)}):
            rc, out, _ = self.run_cli("locate")
        self.assertEqual(rc, 0)
        self.assertIn("Days with content: 1", out)
        rc, out, _ = self.run_cli("locate", "--source", str(self.src / "notes.sqlite3"))
        self.assertEqual(rc, 0)

    def test_not_sqlite(self):
        self.src.mkdir()
        (self.src / "notes.sqlite3").write_bytes(b"hello this is not a database")
        rc, _, err = self.run_cli("locate", "--source", str(self.src))
        self.assertEqual(rc, 3)
        self.assertIn("notes.sqlite3", err)

    def test_unsupported_schema(self):
        self.src.mkdir()
        c = sqlite3.connect(self.src / "notes.sqlite3")
        c.execute("CREATE TABLE notes (date TEXT PRIMARY KEY, body TEXT)")
        c.commit()
        c.close()
        rc, _, err = self.run_cli("locate", "--source", str(self.src))
        self.assertEqual(rc, 4)
        self.assertIn("notes.content", err)
        self.assertIn("1.2.0", err)

    def test_wal_only_recent_writes(self):
        self.db([("2026-01-01", "- old", None)])
        self.conn.execute("PRAGMA wal_autocheckpoint=0")
        self.conn.execute("INSERT INTO notes VALUES ('2026-01-02','- only in wal','2026-01-02T01:00:00.000Z')")
        self.assertTrue((self.src / "notes.sqlite3-wal").stat().st_size > 0)
        self.assertEqual(self.export()[0], 0)
        self.assertEqual((self.out / "2026-01-02.md").read_text().split("\n\n", 1)[1], "- only in wal")

    def test_busy_lock(self):
        self.db([("2026-01-01", "- x", None)], wal=False)
        self.conn.execute("BEGIN EXCLUSIVE")
        with mock.patch.object(srcmod, "BUSY_TIMEOUT", 0.2):
            rc, _, err = self.run_cli("locate", "--source", str(self.src))
        self.assertEqual(rc, 3)
        self.assertIn("busy", err)

    def test_source_never_modified(self):
        self.db([("2026-01-01", "- x", None)])
        self.conn.close()
        p = self.src / "notes.sqlite3"
        before = (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        self.export()
        self.assertEqual(before, (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns))


class ListShowTests(Base):
    def setUp(self):
        super().setUp()
        self.db([("2026-10-07", "- a\n  - b", "2026-10-07T10:00:00.000Z"), ("2026-10-08", "- c", None),
                 ("2026-10-09", "-", None)])

    def test_list_newest_first_and_json(self):
        rc, out, _ = self.run_cli("list", "--source", str(self.src))
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("2026-10-08"))
        self.assertIn("Wed, October 7th, 2026", lines[1])
        self.assertIn("unknown", lines[0])
        self.assertEqual(lines[-1], "2 days with content")
        rc, out, _ = self.run_cli("list", "--source", str(self.src), "--json", "--from", "2026-10-08")
        self.assertEqual(json.loads(out)["total"], 1)

    def test_bad_args(self):
        self.assertEqual(self.run_cli("show", "2026-02-30", "--source", str(self.src))[0], 2)
        self.assertEqual(self.run_cli("list", "--source", str(self.src), "--from", "2026-10-09", "--to", "2026-10-01")[0], 2)
        rc, _, err = self.run_cli("list", "--from", "x")
        self.assertEqual(rc, 2)
        self.assertIn("YYYY-MM-DD", err)

    def test_show_matches_export_and_missing(self):
        rc, out, _ = self.run_cli("show", "2026-10-07", "--source", str(self.src))
        self.assertEqual(rc, 0)
        self.export()
        self.assertEqual(out, (self.out / "2026-10-07.md").read_text())
        rc, _, err = self.run_cli("show", "2026-10-09", "--source", str(self.src))
        self.assertEqual((rc, err.strip()), (1, "no note for 2026-10-09"))


class ExportTests(Base):
    def test_bytes_mtime_modes(self):
        body = "- a\r\n\t- tab ünï 日本\n\n  \n"
        self.db([("2026-10-07", body, "2026-10-07T10:00:00.123Z"), ("2026-10-08", "- n", None),
                 ("2026-10-09", "", None), ("bad-key", "- x", None)])
        rc, out, err = self.export()
        self.assertEqual(rc, 1)
        self.assertIn("bad-key", err)
        f = self.out / "2026-10-07.md"
        self.assertEqual(f.read_bytes().split(b"\n\n", 1)[1], body.encode())
        self.assertIn('title: "Wed, October 7th, 2026"\nupdated: 2026-10-07T10:00:00.123Z\nsource: NoteRepo 1.2.0', f.read_text())
        self.assertEqual(dt.datetime.fromtimestamp(f.stat().st_mtime, dt.timezone.utc).isoformat(),
                         "2026-10-07T10:00:00.123000+00:00")
        g = self.out / "2026-10-08.md"
        self.assertNotIn("updated:", g.read_text())
        self.assertEqual(dt.datetime.fromtimestamp(g.stat().st_mtime).strftime("%Y-%m-%d %H:%M"), "2026-10-08 12:00")
        self.assertFalse((self.out / "2026-10-09.md").exists())
        self.assertEqual(oct(f.stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct(self.out.stat().st_mode & 0o777), "0o700")
        self.assertIn("days with content in scope: 3", out)
        self.assertIn("written:      2", out)

    def test_images_all_mimes(self):
        self.db([("2026-01-01", "".join(f"- ![alt{i}](noterepo:image:{{{i}}})\n" for i in range(6)), None)], IMGS)
        ids = [hashlib.sha256(i[2]).hexdigest() for i in IMGS]
        self.conn.execute("UPDATE notes SET content = ?", ("".join(
            f"- ![alt{n}](noterepo:image:{i})\n" for n, i in enumerate(ids)) + f"- ![d](noterepo:image:{ids[0]})\n"
            "- ![gone](noterepo:image:" + "0" * 64 + ")",))
        rc, _, err = self.export()
        self.assertEqual(rc, 1)
        names = sorted(p.name for p in (self.out / "assets").iterdir())
        self.assertEqual(sorted(n.rsplit(".", 1)[1] for n in names), sorted(["png", "jpg", "gif", "webp", "svg", "bin"]))
        self.assertIn("unknown MIME", err)
        self.assertIn("dangling", err)
        text = (self.out / "2026-01-01.md").read_text()
        self.assertIn(f"![alt0](assets/{ids[0][:16]}.png)", text)
        self.assertIn("noterepo:image:" + "0" * 64, text)
        self.assertEqual(hashlib.sha256((self.out / "assets" / f"{ids[0][:16]}.png").read_bytes()).hexdigest(), ids[0])
        self.assertEqual(self.run_cli("verify", str(self.out), "--source", str(self.src))[0], 0)

    def test_prefix_collision_and_hash_mismatch(self):
        fake = "ab" * 8 + "1" * 48
        fake2 = "ab" * 8 + "2" * 48
        self.db([("2026-01-01", f"![x](noterepo:image:{fake})![y](noterepo:image:{fake2})", None)],
                [("image/png", "a.png", b"one", fake), ("image/png", "b.png", b"two", fake2)])
        rc, _, err = self.export()
        self.assertEqual(rc, 1)
        self.assertIn("not the id", err)
        self.assertTrue((self.out / "assets" / f"{fake}.png").exists())
        self.assertTrue((self.out / "assets" / f"{fake2}.png").exists())

    def test_keep_images_and_no_front_matter(self):
        i = hashlib.sha256(PNG).hexdigest()
        self.db([("2026-01-01", f"- ![a](noterepo:image:{i})", None)], [IMGS[0]])
        self.assertEqual(self.run_cli("config", "set", "export.images", "keep")[0], 0)
        self.assertEqual(self.run_cli("config", "set", "export.front_matter", "none")[0], 0)
        self.assertEqual(self.export()[0], 0)
        self.assertEqual((self.out / "2026-01-01.md").read_text(), f"- ![a](noterepo:image:{i})")
        self.assertFalse((self.out / "assets").exists())
        rc, out, _ = self.run_cli("config")
        self.assertIn("export.images = keep   (config file", out)

    def test_empty_days_include_and_export_time(self):
        self.db([("2026-01-01", "-", None)])
        self.assertEqual(self.export("--set", "export.empty_days=include", "--set", "export.mtime=export_time")[0], 0)
        f = self.out / "2026-01-01.md"
        self.assertTrue(f.read_text().endswith("---\n\n"))
        self.assertLess(abs(time.time() - f.stat().st_mtime), 60)

    def test_nothing_to_export_and_dry_run(self):
        self.db([("2026-01-01", "- a", None)])
        rc, _, err = self.export("--from", "2027-01-01", out=self.tmp / "o3")
        self.assertEqual((rc, "nothing to export" in err), (1, True))
        rc, out, _ = self.export("--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("write    2026-01-01", out)
        self.assertFalse(self.out.exists())

    def test_output_errors(self):
        self.db([("2026-01-01", "- a", None)])
        (self.tmp / "file").write_text("x")
        self.assertEqual(self.export(out=self.tmp / "file")[0], 5)
        ro = self.tmp / "ro"
        ro.mkdir()
        ro.chmod(0o500)
        if os.geteuid() != 0:
            self.assertEqual(self.export(out=ro / "sub")[0], 5)
        ro.chmod(0o700)

    def test_unknown_folder_needs_force(self):
        self.db([("2026-01-01", "- a", None)])
        self.out.mkdir()
        (self.out / "2026-01-01.md").write_text("mine")
        (self.out / "other.txt").write_text("keep")
        rc, _, err = self.export()
        self.assertEqual(rc, 5)
        self.assertEqual((self.out / "2026-01-01.md").read_text(), "mine")
        self.assertEqual(self.export("--force")[0], 0)
        self.assertEqual((self.out / "other.txt").read_text(), "keep")

    def test_lock(self):
        self.db([("2026-01-01", "- a", None)])
        (self.out / ".notehatch").mkdir(parents=True)
        lock = self.out / ".notehatch" / "lock"
        lock.write_text(json.dumps({"pid": os.getpid()}))
        rc, _, err = self.export()
        self.assertEqual(rc, 5)
        self.assertIn(str(lock), err)
        old = time.time() - 7200
        lock.write_text(json.dumps({"pid": 2 ** 22 + 12345}))
        os.utime(lock, (old, old))
        rc, _, err = self.export()
        self.assertEqual(rc, 0)
        self.assertIn("stale lock", err)
        self.assertFalse(lock.exists())


class NullKeyTests(Base):
    def test_null_and_integer_keys_are_skipped(self):
        import sqlite3
        self.db([("2026-01-01", "- a", None)])
        c = sqlite3.connect(self.src / "notes.sqlite3")
        c.execute("drop table notes")
        c.execute("create table notes(date, content, updated_at)")
        c.execute("insert into notes values('2026-01-01','- a',NULL),(NULL,'x',NULL),(7,'y',NULL)")
        c.commit(); c.close()
        rc, _, err = self.export()
        self.assertEqual(rc, 1)
        self.assertNotIn("Traceback", err)
        self.assertTrue((self.out / "2026-01-01.md").exists())


class IncrementalTests(Base):
    def setUp(self):
        super().setUp()
        self.db([(f"2026-09-{d:02d}", f"- note {d}", f"2026-09-{d:02d}T10:00:00.000Z") for d in range(1, 6)])
        self.assertEqual(self.export()[0], 0)

    def mt(self):
        return {p.name: p.stat().st_mtime_ns for p in self.out.glob("*.md")}

    def test_changed_edited_gone(self):
        (self.out / "2026-09-02.md").write_text("my edit")
        before = self.mt()
        self.conn.execute("UPDATE notes SET content='- changed', updated_at='2026-10-01T00:00:00.000Z' WHERE date='2026-09-03'")
        self.conn.execute("DELETE FROM notes WHERE date='2026-09-05'")
        rc, out, _ = self.export()
        self.assertEqual(rc, 1)
        self.assertIn("conflict 2026-09-02", out)
        self.assertIn("gone from source 2026-09-05", out)
        after = self.mt()
        self.assertNotEqual(before["2026-09-03.md"], after["2026-09-03.md"])
        for k in ("2026-09-01.md", "2026-09-02.md", "2026-09-04.md", "2026-09-05.md"):
            self.assertEqual(before[k], after[k], k)
        self.assertEqual((self.out / "2026-09-02.md").read_text(), "my edit")
        self.assertIn("files written:      1", out)
        rc, out, _ = self.run_cli("verify", str(self.out), "--source", str(self.src))
        self.assertEqual(rc, 1)
        self.assertIn("2026-09-02", out)
        self.assertTrue((self.out / "2026-09-05.md").exists())

    def test_overwrite_edited(self):
        (self.out / "2026-09-02.md").write_text("my edit")
        self.assertEqual(self.export("--set", "export.edited_files=overwrite")[0], 0)
        self.assertIn("- note 2", (self.out / "2026-09-02.md").read_text())

    def test_setting_change_rewrites_and_no_op_rerun(self):
        rc, out, _ = self.export("--set", "export.front_matter=none")
        self.assertIn("files written:      5", out)
        before = self.mt()
        self.assertEqual(self.export("--set", "export.front_matter=none")[0], 0)
        self.assertEqual(before, self.mt())

    def test_corrupt_manifest_and_rebuild(self):
        (self.out / ".notehatch" / "manifest.json").write_text("{nope")
        rc, _, err = self.export()
        self.assertEqual(rc, 5)
        self.assertIn("--rebuild-manifest", err)
        (self.out / "2026-09-02.md").write_text("my edit")
        rc, out, _ = self.export("--rebuild-manifest")
        self.assertIn("conflict 2026-09-02", out)

    def test_different_source(self):
        other = self.tmp / "other"
        make_db(other, [("2026-01-01", "- z", None)])
        rc, _, err = self.run_cli("export", str(self.out), "--source", str(other))
        self.assertEqual(rc, 2)
        self.assertIn(str(other), err)
        self.assertEqual(self.run_cli("export", str(self.out), "--source", str(other), "--force")[0], 0)

    def test_interrupt_and_resume(self):
        out2 = self.tmp / "out2"
        count = []

        def hook(res):
            count.append(1)
            if len(count) == 2:
                raise KeyboardInterrupt
        with mock.patch.object(exp, "_after_write", hook):
            rc, _, err = self.export(out=out2)
        self.assertEqual(rc, 130)
        self.assertEqual(len(list(out2.glob("*.md"))), 2)
        self.assertEqual([p for p in out2.rglob("*") if p.name.startswith(".notehatch-tmp")], [])
        self.assertFalse((out2 / ".notehatch" / "lock").exists())
        self.assertEqual(len(json.loads((out2 / ".notehatch" / "manifest.json").read_text())["files"]), 2)
        rc, out, _ = self.export(out=out2)
        self.assertEqual(rc, 0)
        self.assertIn("unchanged:          2", out)
        self.assertEqual(self.run_cli("verify", str(out2), "--source", str(self.src))[0], 0)

    def test_verify_problems(self):
        self.assertEqual(self.run_cli("verify", str(self.tmp), "--source", str(self.src))[0], 2)
        rc, out, _ = self.run_cli("verify", str(self.out), "--source", str(self.src))
        self.assertEqual((rc, out.splitlines()[-1]), (0, "OK: 5 notes, 0 images match source"))
        (self.out / "2026-09-01.md").unlink()
        (self.out / "2026-08-30.md").write_text("stray")
        self.conn.execute("UPDATE notes SET content='- newer' WHERE date='2026-09-04'")
        rc, out, _ = self.run_cli("verify", str(self.out), "--source", str(self.src), "--json")
        rep = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual((rep["missing"], rep["out_of_date"], rep["orphaned"]), (["2026-09-01"], ["2026-09-04"], ["2026-08-30.md"]))


class ConfigTests(Base):
    def test_config_errors(self):
        self.assertEqual(self.run_cli("config", "set", "export.nope", "x")[0], 2)
        rc, _, err = self.run_cli("config", "set", "export.images", "bogus")
        self.assertEqual(rc, 2)
        self.assertIn("extract, keep", err)
        Path(os.environ["NOTEHATCH_CONFIG"]).write_text("[export]\nimages = \n")
        rc, _, err = self.run_cli("config")
        self.assertEqual(rc, 2)
        self.assertIn("cfg.toml", err)
        self.assertIn("line 2", err)

    def test_precedence(self):
        self.run_cli("config", "set", "export.images", "keep")
        with mock.patch.dict(os.environ, {"NOTEHATCH_EXPORT_IMAGES": "extract"}):
            _, out, _ = self.run_cli("config")
            self.assertIn("export.images = extract   (env NOTEHATCH_EXPORT_IMAGES)", out)
            _, out, _ = self.run_cli("config", "--set", "export.images=keep")
            self.assertIn("export.images = keep   (flag --set)", out)
        with mock.patch.dict(os.environ, {"NOTEHATCH_EXPORT_IMAGES": "zzz"}):
            self.assertEqual(self.run_cli("config")[0], 2)


class DemoTests(Base):
    def test_demo_roundtrip(self):
        d = self.tmp / "demo"
        self.assertEqual(self.run_cli("demo", str(d))[0], 0)
        rc, _, err = self.run_cli("demo", str(d))
        self.assertEqual(rc, 2)
        self.assertIn("notes.sqlite3", err)
        rc, out, _ = self.run_cli("locate", "--source", str(d))
        for line in ("Days with content: 70", "Empty rows (will be skipped): 6", "Images: 6", "without updated_at: 5"):
            self.assertIn(line, out)
        rc, out, err = self.run_cli("export", str(self.out), "--source", str(d))
        self.assertEqual(rc, 1)
        self.assertIn("days with content in scope: 71", out)
        self.assertEqual(len(list(self.out.glob("*.md"))), 70)
        self.assertEqual(self.run_cli("verify", str(self.out), "--source", str(d))[0], 0)


if __name__ == "__main__":
    unittest.main()


class SafetyTests(Base):
    def test_output_inside_source_refused(self):
        self.db([("2026-01-01", "- a", None)])
        before = sorted(p.name for p in self.src.iterdir() if not p.name.endswith(("-wal", "-shm")))
        for target in (self.src, self.src / "sub"):
            self.assertEqual(self.export(out=target)[0], 5)
        self.assertEqual(before, sorted(p.name for p in self.src.iterdir() if not p.name.endswith(("-wal", "-shm"))))

    def test_verify_lists_any_stray_file(self):
        self.db([("2026-01-01", "- a", None)])
        self.export()
        (self.out / "stray.md").write_text("s")
        rc, out, _ = self.run_cli("verify", str(self.out), "--source", str(self.src), "--json")
        self.assertIn("stray.md", json.loads(out)["orphaned"])


class UnknownFolderTests(Base):
    def test_both_values(self):
        self.db([("2026-01-01", "- a", None)])
        self.out.mkdir()
        (self.out / "other.txt").write_text("keep")
        rc, _, err = self.export()  # default refuse_any_files
        self.assertEqual(rc, 5)
        self.assertIn("other.txt", err)
        self.assertFalse((self.out / "2026-01-01.md").exists())
        self.assertEqual(self.export("--set", "export.unknown_folder=refuse_on_overlap")[0], 0)
        self.assertTrue((self.out / "2026-01-01.md").exists())

    def test_env_overrides_flag(self):
        self.db([("2026-01-01", "- a", None)])
        self.out.mkdir()
        (self.out / "other.txt").write_text("keep")
        with mock.patch.dict(os.environ, {"EXPORT_UNKNOWN_FOLDER": "refuse_any_files"}):
            self.assertEqual(self.export("--set", "export.unknown_folder=refuse_on_overlap")[0], 5)
        with mock.patch.dict(os.environ, {"EXPORT_UNKNOWN_FOLDER": "refuse_on_overlap"}):
            self.assertEqual(self.export()[0], 0)


class UnchangedExitTests(Base):
    def test_both_values(self):
        self.db([("2026-01-01", "- a", None)])
        self.assertEqual(self.export()[0], 0)
        self.assertEqual(self.export()[0], 0)  # default success
        self.assertEqual(self.export("--set", "export.unchanged_exit=warn")[0], 1)
        with mock.patch.dict(os.environ, {"EXPORT_UNCHANGED_EXIT": "success"}):
            self.assertEqual(self.export("--set", "export.unchanged_exit=warn")[0], 0)


class VerifyScopeTests(Base):
    def test_both_values(self):
        self.db([("2026-09-01", "- a", None), ("2026-10-01", "- b", None)])
        self.export("--from", "2026-09-01", "--to", "2026-09-30")
        rc, out, _ = self.run_cli("verify", str(self.out), "--source", str(self.src), "--json")
        self.assertEqual((rc, json.loads(out)["missing"]), (1, ["2026-10-01"]))
        rc, out, _ = self.run_cli("verify", str(self.out), "--source", str(self.src), "--set", "verify.scope=manifest_days")
        self.assertEqual((rc, out.splitlines()[-1]), (0, "OK: 1 notes, 0 images match source"))
        with mock.patch.dict(os.environ, {"VERIFY_SCOPE": "all_source_days"}):
            self.assertEqual(self.run_cli("verify", str(self.out), "--source", str(self.src),
                                          "--set", "verify.scope=manifest_days")[0], 1)


class MissingFilesTests(Base):
    def test_both_values(self):
        self.db([("2026-01-01", "- a", None), ("2026-01-02", "- b", None)])
        self.export()
        (self.out / "2026-01-01.md").unlink()
        rc, out, _ = self.export("--set", "export.missing_files=conflict")
        self.assertEqual(rc, 1)
        self.assertIn("conflict 2026-01-01: deleted from export", out)
        self.assertFalse((self.out / "2026-01-01.md").exists())
        rc, out, _ = self.export()
        self.assertEqual(rc, 0)
        self.assertTrue((self.out / "2026-01-01.md").exists())
        (self.out / "2026-01-01.md").unlink()
        with mock.patch.dict(os.environ, {"EXPORT_MISSING_FILES": "conflict"}):
            self.assertEqual(self.export("--set", "export.missing_files=rewrite")[0], 1)


class StaleLockTests(Base):
    def fresh_dead_lock(self):
        (self.out / ".notehatch").mkdir(parents=True, exist_ok=True)
        lock = self.out / ".notehatch" / "lock"
        lock.write_text(json.dumps({"pid": 2 ** 22 + 12345}))
        return lock

    def test_both_values(self):
        self.db([("2026-01-01", "- a", None)])
        lock = self.fresh_dead_lock()
        rc, _, err = self.export()  # default false: a dead owner's lock is removed at once
        self.assertEqual(rc, 0)
        self.assertIn("stale lock", err)
        lock = self.fresh_dead_lock()
        rc, _, err = self.export("--set", "lock.stale_requires_age=true")
        self.assertEqual(rc, 5)
        self.assertIn(str(lock), err)
        with mock.patch.dict(os.environ, {"LOCK_STALE_REQUIRES_AGE": "true"}):
            self.assertEqual(self.export("--set", "lock.stale_requires_age=false")[0], 5)


class EnvPrecedenceTests(Base):
    def test_both_values(self):
        a, b = self.tmp / "a", self.tmp / "b"
        make_db(a, [("2026-01-01", "- a", None)])[0].close()
        make_db(b, [("2026-01-01", "- b1", None), ("2026-01-02", "- b2", None)])[0].close()
        env = {"NOTEHATCH_SOURCE": str(a), "NOTEREPO_DATA_DIR": str(b)}
        with mock.patch.dict(os.environ, env):
            _, out, _ = self.run_cli("locate")  # default notehatch_first
            self.assertIn(str(a), out)
            _, out, _ = self.run_cli("locate", "--set", "source.env_precedence=noterepo_first")
            self.assertIn(str(b), out)
            with mock.patch.dict(os.environ, {"SOURCE_ENV_PRECEDENCE": "notehatch_first"}):
                _, out, _ = self.run_cli("locate", "--set", "source.env_precedence=noterepo_first")
                self.assertIn(str(a), out)


class MtimeChangeTests(Base):
    def test_both_values(self):
        self.db([("2026-01-01", "- a", "2026-01-01T10:00:00.000Z")])
        self.export()
        f = self.out / "2026-01-01.md"
        old = f.stat().st_mtime_ns
        rc, out, _ = self.export("--set", "export.mtime=export_time", "--set", "export.mtime_change_rewrites=false")
        self.assertEqual((rc, f.stat().st_mtime_ns), (0, old))
        rc, out, _ = self.export("--set", "export.mtime=export_time")  # default true
        self.assertIn("files written:      1", out)
        self.assertGreater(f.stat().st_mtime_ns, old)
        self.assertEqual(self.export("--set", "export.mtime=export_time")[0], 0)  # now stable
        with mock.patch.dict(os.environ, {"EXPORT_MTIME_CHANGE_REWRITES": "false"}):
            before = f.stat().st_mtime_ns
            self.export()  # back to updated, but env says false
            self.assertEqual(f.stat().st_mtime_ns, before)
