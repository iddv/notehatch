# AGENTS.md — NoteHatch

NoteHatch is an independent implementation, not affiliated with or endorsed by NoteRepo's maker (Flavio Copes), and it was built against NoteRepo 1.2.0.

Built by an autonomous build (see NOTICE); maintained through GitHub issues by the owner, by
bears, and by any coding agent that reads this file. CLAUDE.md imports this file.

## What this is

- **For:** see SCOPE.md › Users
- **Does:** NoteHatch is a command-line tool that a single person runs on their own computer to get their daily notes out of NoteRepo. It reads NoteRepo's local database directly and read-only, without needing NoteRepo to be running or modified. It writes every day that has content to its own plain Markdown file named by date.
- **Stack:** Python 3.11+, standard library only, a command-line tool.
- **Scope:** `SCOPE.md` (users, numbered flows F1–F7, domain rules, definition of done).
  `ASSUMPTIONS.md` lists the questions the idea left open and what this version does.

## Setup, run, test

These commands are run by CI exactly as written (`.github/workflows/ci.yml`, job `agents-md`).
If you change a command, change it here in the same commit. A line marked `# serve` starts the
service; the check waits for it to answer on 127.0.0.1, then stops it.

```sh
python -m pip install .   # install
python -m unittest tests.test_notehatch   # the product's own tests
```

- pytest is only needed for the acceptance suite; install it inside a virtual environment if
  your system Python refuses.
- Demo data: `see README` (empty install only).
- Backup and restore: `see README`, `see README`.
- Configuration: environment variables or `a config file` (see README › Configuration).

## Architecture in brief

| Path | Holds |
|---|---|
| `notehatch/__main__.py` | Runs the package as a command: `python -m notehatch`. |
| `notehatch/cli.py` | Defines `err`, `warn`, `Parser`, `build_parser` and 11 more. |
| `notehatch/config.py` | Defines `env_name`, `default_config_path`, `check_value`, `config_path` and 4 more. |
| `notehatch/dates.py` | Defines `parse_key`, `ordinal`, `title`, `utc_to_ns` and 3 more. |
| `notehatch/demo.py` | Builds a realistic NoteRepo 1.2.0 database for trying the tool. |
| `notehatch/errors.py` | Defines `NHError`. |
| `notehatch/export.py` | Export planning and writing; also used by `show` and `verify`. |
| `notehatch/fsutil.py` | Defines `hash_file`, `out_error`, `atomic_write`, `make_dir` and 3 more. |
| `notehatch/manifest.py` | Defines `manifest_path`, `new`, `load`, `save`. |
| `notehatch/render.py` | Turning a note row into the bytes of its Markdown file. |
| `notehatch/source.py` | Defines `find_source`, `busy_error`, `translate`, `Source`. |
| `notehatch/verify.py` | Defines `run`. |
| `tests/test_notehatch.py` | Unit tests (the product's own test command). |

Data lives in `./data`; the schema is versioned (see README), and a
pre-migration backup is written before any upgrade.

Gone days are kept and listed, never deleted. 4.

## Conventions

- Standard library only. Do not add a dependency to fix a bug.
- Operator settings (binary business rules) are settings with an environment override; a new one
  goes in the config file and flags, the README table, `ASSUMPTIONS.md`, and gets an acceptance test per
  value. Never hard-code one side of a business rule.
- Every behaviour has one acceptance test file, `tests/acceptance/test_<what_it_checks>.py`,
  with a plain docstring (what it checks, what is expected, where the rule comes from). Helpers
  live in `tests/acceptance/_helpers/` and drive the product's real interface.
- Plain words in user-facing files: no build-process jargon, no internal ids.

## The acceptance suite, readiness and security

- `tests/acceptance/README.md` lists the **known open items**: tests marked
  `xfail(strict=False)` because the behaviour is not there yet. **To fix one:** make the test
  pass, then delete the mark block at the end of its file and the line in that README. CI fails
  if an xfail test now passes and still carries the mark.
- `READINESS.md` is the operational scorecard (install, localhost default, empty start, first
  admin, backup, restart, logs, CI, docs). Keep it true: a change that moves an item from amber
  to green edits the row.
- `SECURITY.md` is the threat model, what was probed, and the findings with their status. A fix
  for a finding changes its status there and names its test. Report new weaknesses privately
  (see its last section), never in a public issue.

## Operator settings

None yet. A new one follows the operator-settings convention above.

## Maintenance protocol (issues and labels)

Labels are the state; GitHub is the only record. `.github/labels.yml` defines them.

1. **Pick** an open issue labelled `go` with no assignee and no `claim:*` label (or one whose
   lease comment has expired and that has no open linked PR). `p1` before `p2` before `p3`.
2. **Claim** it: a person assigns themself; an agent adds `claim:agent` and a comment
   `<!-- bears:claim lease_until=<UTC+6h> branch=<branch> -->  Working on this.` Leave issues
   with `claim:bears` or an assignee alone.
3. **Branch** from `main`: `<who>/<N>-<slug>` (bears uses `bears/<N>-<slug>`).
4. **Reproduce first:** write `tests/acceptance/test_<slug>.py` that fails on `main` for the
   reason the issue describes. If you cannot make it fail, label `cannot-reproduce`, say why in a
   comment, remove your claim, and stop.
5. **Fix** with the smallest change that makes it pass without weakening another test.
6. **Verify:** run every command in *Setup, run, test*; the whole suite must be green; the
   README's quick start must still work as written.
7. **Documents:** update this file if a command, module or convention changed; `READINESS.md`
   or `SECURITY.md` if a row changed. Leave `CHANGELOG.md` alone: the release notes list the
   merged pull requests.
8. **Open a PR** with the template (`.github/pull_request_template.md`): what and why, the
   failing test's name, the verification you ran, and `Fixes #N`. Remove your claim label; the
   PR is now the lock. **Never push to `main`. Never change `.github/workflows/` in a fix PR.**
   Never commit a secret, a database file or demo credentials.
9. The owner reviews and merges on GitHub. A release follows (`.github/bears.yml` ›
   `release.on_merge`).

bears follows exactly these steps; its comments carry `<!-- bears:… -->` markers and the PR
label `bears`. Policy for this repo: `.github/bears.yml`.

## Provenance

Generated by an autonomous software build from a short product brief, with automated checks (its tests, a walk of every documented flow, a security review and a readiness review) before release. See `NOTICE`, `LICENSE` and `READINESS.md`.
