# Scope: NoteHatch

NoteHatch is a command-line tool that a single person runs on their own computer to get their daily notes out of NoteRepo. It reads NoteRepo's local database directly and read-only, without needing NoteRepo to be running or modified. It writes every day that has content to its own plain Markdown file named by date. Each file keeps the note text byte-for-byte, the note's date and its last-modified time, and the note's images are saved as ordinary files. v1 covers four jobs: a one-off export when moving to another app, a repeatable backup that only rewrites what changed, a check that a past export still matches the source, and browsing what is there before exporting.

## Users

- **Operator (the only role):** the person who owns the NoteRepo notes, working at a terminal on the same machine (or with a copy of the data folder or a NoteRepo backup).
  - They need to find their data without knowing where it lives, export it safely while NoteRepo may still be open, and trust that nothing was lost or altered.
  - They get in by running the `notehatch` command as their own OS user. There are no accounts, logins or passwords, and no first-run admin account. The OS user's file permissions are the only access control.
  - NoteHatch never listens on a network port and never sends data anywhere.

## Studied behaviour

The target studied was NoteRepo 1.2.0, an Electron desktop app with a companion CLI. v1 must match the following.

**Where the data lives**
- The data is a single SQLite file named `notes.sqlite3` inside NoteRepo's data folder.
- Candidate locations, tried in this order:
  1. an explicit `--source` option;
  2. the `NOTEREPO_DATA_DIR` env var (NoteRepo's own override);
  3. macOS: `~/Library/Application Support/NoteRepo`;
  4. Linux: `~/.config/NoteRepo` (unverified);
  5. Windows: `%APPDATA%\NoteRepo` (unverified).
- A NoteRepo backup (`NoteRepo/backups/<name>/notes.sqlite3`) has the same format and is a valid source. `--source` accepts either a folder or a `.sqlite3` file.
- The database uses WAL mode, so recent writes may sit in `notes.sqlite3-wal`. NoteHatch must open the database through SQLite in read-only mode, never by copying the main file alone. It must wait up to 5 seconds on a busy lock.
- The app may be writing during an export, and it polls for changes about every 500 ms.

**Tables used**
- `notes`:
  - There is one row per calendar day.
  - `date` is a local-calendar `YYYY-MM-DD` text key. It must never be converted through UTC.
  - `content` is the note text.
  - `updated_at` is an ISO-8601 UTC timestamp with milliseconds and a `Z` suffix. It may be NULL for notes that were migrated from an older format.
  - No creation time is stored.
- `images`:
  - `id` is the lowercase 64-hex SHA-256 of the bytes and equals `checksum`.
  - The table also holds `mime_type` (one of png, jpeg, gif, webp, svg+xml), the original `file_name` (names may repeat), `data` (the bytes) and size fields.
- The `metadata` table and the legacy `notes.json` file are ignored in v1.

**Content format**
- `content` is a Markdown-like outline with `\n` line breaks.
  - Items are `- ` bullets and `N. ` numbered items.
  - Each level of nesting is two spaces or one tab.
  - Content also holds `[label](url)` links and bare `http(s)` URLs, including X post links.
  - It is already valid Markdown and is exported verbatim.
- Image references have the form `![alt](noterepo:image:<64-hex id>)`. This is a private scheme that other apps cannot resolve.
- A day "has content" if at least one line, once trimmed, is neither empty nor just a bare `-` or `N.` marker. Days without content are hidden in the app, and the table may still hold such rows.

**Title**
- The app shows each day's title in the form `Wed, October 7th, 2026`: English short weekday, full month name, ordinal day, and year.
- Ordinals follow the usual rules: 1st, 2nd, 3rd, 4th…; 11th, 12th and 13th use "th"; 21st, 22nd, 23rd, 31st.
- The title is derived from the date key and is not stored in the content.

**Not handled**
- Schemas from versions other than 1.2.0 were not studied. NoteHatch must detect a schema it doesn't understand and refuse to read it (see Domain rules).

## Core flows

### F1: Install and first run
- Who: Operator
- Steps:
  1. The operator runs the single install command from the README. `notehatch` is now on their PATH.
  2. They run `notehatch --version` and see the version and the NoteRepo version studied (1.2.0).
  3. They run `notehatch locate`. NoteHatch tries the candidate locations, opens the first database it finds read-only and checks the schema. It prints the path used, the number of days with content, the number of empty rows that will be skipped, the number of images, the date range covered, and how many notes have no `updated_at`.
  4. To try the tool without real notes, they run `notehatch demo ./demo-notes`. This writes a demo NoteRepo-format database (see Install and run). They then pass `--source ./demo-notes` to any other command.
- When it goes wrong:
  - No database is found: the tool lists every path it tried and suggests `--source`. Exit code 3.
  - The file is not SQLite or cannot be read (for example, a permissions problem): the tool names the file and gives the OS reason. Exit code 3.
  - The schema is unsupported: the tool names the missing table or column and says only NoteRepo 1.2.0 was studied. Exit code 4.
  - The demo target already holds a `notes.sqlite3`: the tool refuses and names the file. Exit code 2.

### F2: Find and preview notes
- Who: Operator
- Steps:
  1. The operator runs `notehatch list`, with optional `--from YYYY-MM-DD`, `--to YYYY-MM-DD` and `--json`.
  2. NoteHatch prints one line per day with content, newest first. Each line shows the date, the title, the last-updated time in local time (or "unknown"), the line count and the image count. A total line follows.
  3. The operator runs `notehatch show 2026-10-07`, with optional `--json`. They see exactly the Markdown file that an export would write for that day (front matter and body, following the current settings), plus a list of the images it references.
- When it goes wrong:
  - The date is malformed or not a real date (`2026-02-30`): the tool says "invalid date", shows the expected format, and exits with code 2.
  - `--from` is later than `--to`: the tool explains this and exits with code 2.
  - The day has no row or no content: the tool prints "no note for 2026-10-07" and exits with code 1.

### F3: Export all notes
- Who: Operator
- Steps:
  1. The operator runs `notehatch export ~/NotesExport`.
  2. NoteHatch takes a lock on the output folder, opens the source read-only and reads every note and image inside one read transaction, so the export is a consistent snapshot.
  3. For each day with content, it writes `YYYY-MM-DD.md` and sets the file's modification time. With `export.images = extract` it also saves the referenced images under `assets/` and rewrites their links.
  4. It writes the manifest and prints a summary: files written, empty days skipped, images saved, warnings (each with its date), output path and elapsed time.
- When it goes wrong:
  - The output folder is not writable or the disk is full: the tool names the file it was writing, and no half-written file is left behind. Exit code 5.
  - Another NoteHatch run holds the lock: the tool refuses and names the lock file. Exit code 5.
  - The database is locked for longer than 5 seconds: the tool says NoteRepo is busy and asks the operator to retry. Exit code 3.
  - A date key is invalid, or an image reference is dangling: the tool skips that item or leaves it as is, prints a warning, and the export completes. Exit code 1.
  - The output folder is non-empty, has no manifest and has a file that would be overwritten: the tool refuses unless `--force` is given. Exit code 5.

### F4: Re-export as a backup (incremental)
- Who: Operator
- Steps:
  1. Later, the operator runs `notehatch export ~/NotesExport` again into the same folder.
  2. NoteHatch compares the source with the manifest and classifies every day as new, changed, unchanged, edited-in-export (the file's hash differs from the manifest) or gone-from-source.
  3. It writes the new and changed files and leaves unchanged files untouched, including their modification times. Edited files are handled according to `export.edited_files`. Gone days are kept and listed, never deleted.
  4. The summary shows the count in each class, and the conflicts are listed by date.
- When it goes wrong:
  - The manifest is corrupt: the tool explains this and suggests `--rebuild-manifest`, which re-hashes the existing files and treats them as edited-in-export. Exit code 5.
  - The manifest came from a different source: the tool warns and shows both paths, and continues only with `--force`. Exit code 2.

### F5: Partial export, dry run and cancel
- Who: Operator
- Steps:
  1. The operator runs `notehatch export DIR --from 2026-09-01 --to 2026-09-30 --dry-run`.
  2. They see the per-day plan (write, skip, conflict), the image count and the total bytes. Nothing is written.
  3. They rerun the command without `--dry-run`. Only days in that range are written, and the manifest gains only those entries.
  4. If they press Ctrl-C during an export, files already finished stay in place and the manifest records exactly those. No partial file remains, and the lock is released. Rerunning the export finishes the job.
- When it goes wrong:
  - The range contains no days with content: the tool prints "nothing to export" and exits with code 1.
  - The dates are invalid: the tool reports them as in F2.

### F6: Verify an export (end-of-month backup check)
- Who: Operator
- Steps:
  1. The operator runs `notehatch verify ~/NotesExport`, with optional `--json`.
  2. NoteHatch rereads the source and the export folder. For every day with content in the source, it checks that the file exists, that the body equals the source content byte-for-byte, that the front matter dates are right, and that each image file's SHA-256 equals its id.
  3. The tool prints a report:
     - verified count;
     - missing, out-of-date and edited files, each listed by date;
     - orphaned files;
     - bad images;
     - a final line "OK: N notes, M images match source" or "PROBLEMS: …".
- When it goes wrong:
  - The folder is not a NoteHatch export (no manifest): the tool says so. Exit code 2.
  - Any mismatch is found: exit code 1, with the list above.

### F7: Change export settings
- Who: Operator
- Steps:
  1. `notehatch config` lists every setting with its value and source (default, config file, env var or flag).
  2. `notehatch config set export.images keep` checks the value and saves it to the config file.
  3. Later exports use it, and `show` previews the effect.
- When it goes wrong:
  - The key is unknown: the tool lists the valid keys. Exit code 2.
  - The value is invalid: the tool lists the allowed values. Exit code 2.
  - The config file is unreadable or malformed: the tool names the file and the line. Exit code 2.

## Features

### Must have (v1)
- M1: Source discovery across `--source`, env vars and the platform paths. Folder, file or NoteRepo backup accepted (F1, F2, F3).
- M2: Read-only SQLite access with WAL included, a 5-second busy timeout, a single snapshot transaction and a schema check (F1, F3, F4, F6).
- M3: `locate` summary of the source (F1).
- M4: `demo` command that builds a realistic NoteRepo-format database (F1).
- M5: `list` and `show` with date-range filters and `--json` (F2).
- M6: Markdown writer: date file names, YAML front matter, verbatim body, file modification time, atomic writes (F3, F4, F5).
- M7: Image extraction to `assets/` with MIME-based extensions and relative link rewriting, plus the keep option (F3, F7).
- M8: Manifest, output lock and incremental classification (new, changed, unchanged, edited, gone) (F4, F5, F6).
- M9: `--from` and `--to` filters, `--dry-run`, and clean interrupt handling (F5).
- M10: `verify` report with hash checks (F6).
- M11: Settings through a config file, env vars and flags, with a `config` command (F7).
- M12: Consistent messages and exit codes, a README (install, commands, settings, disclaimer) and an automated test suite (all flows).

### Later (not in v1)
- Reading legacy `notes.json`: only relevant to users who never opened 1.x. It can wait.
- Other output formats (HTML, PDF, single combined file, Obsidian or Logseq-specific outlines): Markdown opens everywhere.
- Writing notes back or importing into NoteRepo: v1 is read-only by design.
- Watch mode or scheduled backups: the OS scheduler can run `export` instead.
- Support for schemas from other NoteRepo versions: these have not been studied.
- Folder layouts by year or month: the flat layout is enough for v1.
- A GUI or installer packages per OS.

## Domain rules and defaults

**What gets exported**
- A day is exported only if it has content, as defined in Studied behaviour (see also `export.empty_days`).
- A date key is accepted only if it is exactly `YYYY-MM-DD` and a real Gregorian date. Any other key is skipped with a warning. Keys are never used raw in a path.

**File naming and contents**
- File name: `<date>.md`, flat in the output root **(default)**.
- Body: the `content` value, byte-for-byte as UTF-8. Line endings are not changed, and no newline is added or removed. The only exception is image link rewriting under `extract`.
- Front matter (when enabled) uses these keys:
  - `date`, the key;
  - `title`, quoted, in the app's title format;
  - `updated`, the original UTC string, omitted when NULL;
  - `source: NoteRepo 1.2.0`.
- Front matter is followed by one blank line before the body.

**File modification time**
- When `updated_at` is present, the file's modification time is set to `updated_at`.
- When `updated_at` is NULL, it is set to 12:00 local time on the note's date **(default)**.

**Images**
- Images are saved as `assets/<first 16 hex of id>.<ext>` **(default)**. If two ids share that prefix, both use the full id.
- Extensions by MIME type: png→`.png`, jpeg→`.jpg`, gif→`.gif`, webp→`.webp`, svg+xml→`.svg`.
- An unknown MIME type uses `.bin` and produces a warning.
- Under `extract`, a link becomes `![alt](assets/<name>)`. The alt text is kept.
- Only images referenced by an exported note are written.
- An image is written once, however many notes use it.
- A dangling reference stays verbatim and produces a warning.
- If an image's bytes do not hash to its id, the image is still written and a warning is printed.

**Manifest and lock**
- The manifest lives in `<out>/.notehatch/manifest.json`. It records:
  - the tool version and the source path;
  - for each file: its date, the source `updated_at`, the SHA-256 of the source content, the SHA-256 of the written file and the export time;
  - for each image: its id and file name.
- The lock is `<out>/.notehatch/lock`. A lock older than 1 hour whose PID is not running is treated as stale and removed with a notice **(default)**.

**Change detection**
- "Changed" means the source content hash or `updated_at` differs from the manifest. A changed setting that alters the output also counts.
- "Edited" means the file on disk no longer matches the hash recorded in the manifest.

**Atomic writes**
- Every write goes to a temp file in the same folder and is then renamed into place.
- The manifest is updated after each file is written. On interrupt, the last completed state is flushed.

**Dates and output**
- Times are shown in local time. Dates are always the stored key, never recomputed.
- Exit codes:
  - 0: success;
  - 1: completed with warnings, conflicts or nothing to do;
  - 2: usage or config error;
  - 3: source not found, unreadable or busy;
  - 4: unsupported schema;
  - 5: output error.
- Output files are created with mode 0600 and folders with mode 0700 on POSIX **(default)**, matching the privacy of the source.

## Operator settings

| key | values | default | what it decides |
|---|---|---|---|
| `export.front_matter` | yaml, none | yaml | Whether each file starts with YAML front matter (date, title, updated) or holds only the note body. |
| `export.images` | extract, keep | extract | Whether images are saved under `assets/` with links rewritten, or the original `noterepo:image:` references are left untouched. |
| `export.edited_files` | keep, overwrite | keep | On re-export, whether a file the operator edited after export is left alone and reported as a conflict, or replaced with the source version. |
| `export.mtime` | updated, export_time | updated | Whether file modification times follow the note's last update or the moment of export. |
| `export.empty_days` | skip, include | skip | Whether rows without content are skipped or exported as files with an empty body. |

## Money and data integrity

NoteHatch handles no money. Its integrity rules are:

**The source is never changed**
- The source is opened read-only, and no command ever writes to it.
- The data folder or source file is never written to, locked for writing, copied over or deleted.

**Nothing is lost or counted twice**
- Every day with content in the snapshot produces exactly one file. Otherwise it is listed by date as a skipped item with a warning.
- The summary line always satisfies: written + unchanged + conflicts + skipped-invalid = days with content in scope.
- Every image is written once, keyed by its id.

**Operator files are protected**
- NoteHatch never deletes files in the output folder.
- Edited files are only replaced under `export.edited_files = overwrite`.
- An unknown folder that holds files is only written into with `--force`.

**Concurrency**
- The NoteRepo app writing at the same time is handled by the single snapshot read.
- Two NoteHatch runs on the same output folder are prevented by the lock.

**Validation before writing**
- The schema, every date key, every image id format, all settings and the writability of the output folder are checked before the first file is written.

## Install and run

**Install**
- One documented command installs NoteHatch from the project folder and puts `notehatch` on the PATH on macOS, Linux and Windows.
- It needs no network service and no NoteRepo installation.

**Safe defaults**
- NoteHatch opens no network ports.
- It has no accounts or secrets.
- It reads the real data read-only.
- It writes only to the output folder named on the command line.

**Demo data**
- `notehatch demo DIR` writes `DIR/notes.sqlite3` in the NoteRepo 1.2.0 schema.
- The database covers about 90 days over 3 months, with 70 days of content. The content includes:
  - nested and numbered outlines, tab indentation, links, bare URLs and X post URLs;
  - non-ASCII text;
  - one note near 100 KB.
- It also holds:
  - 6 empty or whitespace-only rows, including bare-marker rows;
  - 5 notes with NULL `updated_at`;
  - 6 images, one of each MIME type plus one image reused across two notes;
  - one dangling image reference;
  - one malformed date key.
- The demo is loaded only by this explicit command.

**Configuration**
- Settings are resolved in this order of precedence:
  1. flags (`--source`, `--config`, `--force`, and `--set key=value`);
  2. env vars (`NOTEHATCH_SOURCE`, `NOTEHATCH_CONFIG`, `NOTEREPO_DATA_DIR`, and `NOTEHATCH_<KEY>` with dots as underscores, such as `NOTEHATCH_EXPORT_IMAGES`);
  3. the config file (TOML or similar plain key=value) in the platform's user config folder;
  4. defaults.
- Every option is documented in the README.

**Data, backup and upgrade**
- NoteHatch's only state is the manifest in each export folder and its config file.
- Backing up an export means copying the folder. Restoring means copying it back, after which `verify` confirms it matches.
- The manifest carries a format version. Newer NoteHatch versions read older manifests and upgrade them in place.

**README**
- The README states in one sentence that NoteHatch is an independent implementation, not affiliated with or endorsed by NoteRepo's maker (Flavio Copes), and that it was built against NoteRepo 1.2.0.

## Non-functional basics

- **Access:** the OS user only. There are no roles.
- **Input validation:** every argument and setting is checked before work starts. Errors are one line naming the bad value and the expected form. `--help` is available on every command.
- **Output:** human-readable by default and `--json` where listed. Warnings go to stderr. Results are deterministic, so the same snapshot and settings give byte-identical files.
- **Performance:** exporting 5,000 notes and 500 MB of images completes without loading all image bytes into memory at once.
- **Tests:** one documented command runs the test suite. The suite builds its own fixture databases and covers:
  - WAL-only recent writes and a busy lock;
  - NULL `updated_at`, empty and bare-marker rows, and bad keys;
  - every MIME type, dangling and repeated images, and hash mismatches;
  - title ordinals (1st, 2nd, 3rd, 11th, 12th, 13th, 21st, 22nd, 23rd);
  - the incremental classes, interrupt recovery, `verify` mismatches and the settings.

## Definition of done

- [ ] F1: Install and first run — after the README install command, `notehatch locate --source ./demo-notes` (following `notehatch demo ./demo-notes`) prints the path and the counts of days, empty rows, images and NULL timestamps. A missing source lists the tried paths and exits with code 3.
- [ ] F2: Find and preview notes — `list` shows 70 demo days newest first with titles like `Wed, October 7th, 2026`. `show` prints the exact file an export would write, and an invalid date exits with code 2.
- [ ] F3: Export all notes — the output folder holds one `.md` file per day with content, with a byte-identical body, correct front matter and modification time, the images under `assets/` and a manifest. The summary totals add up, and the bad key and dangling image are reported as warnings.
- [ ] F4: Re-export as a backup — after one demo note is changed and one exported file is hand-edited, a second export rewrites only the changed file, keeps the edited file and reports it as a conflict, and leaves all other modification times untouched.
- [ ] F5: Partial export, dry run and cancel — `--dry-run` writes nothing and shows the plan. A September-only export writes only September files. A Ctrl-C mid-run leaves no partial file, and a rerun completes the export.
- [ ] F6: Verify an export — `verify` on a fresh export prints "OK" and exits with code 0. After a file is deleted or an image is altered, it lists that item by date or id and exits with code 1.
- [ ] F7: Change export settings — `config set export.images keep` persists the value, `show` and `export` then leave the `noterepo:image:` links in place, and an invalid value exits with code 2 and lists the allowed values.
