"""Keep AGENTS.md honest: run the commands it documents, exactly as written.

1. The first ```sh fence under "## Setup, run, test" is run line by line from the repository
   root (a failing line fails the check). A line whose comment starts with `serve` starts the
   service: the check waits until it answers on 127.0.0.1, then stops it. When the comment
   names the port variable, as in `# serve: http://127.0.0.1:8080/ (port: APP_PORT)`, a free
   port is used instead, so the check never collides with something already running.
2. Every path in the first column of the "## Architecture in brief" table must exist.
3. A known open item (a test marked xfail) that now passes must have had its mark removed: an
   XPASS from a file that still carries the mark fails the check.

Standard library only. Usage: python .github/scripts/check_agents_md.py [REPO_ROOT]
Environment: AGENTS_MD_TIMEOUT (seconds per line, default 600)."""
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                       os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
TIMEOUT = int(os.environ.get("AGENTS_MD_TIMEOUT", "600"))
COMMENT = re.compile(r"\s+#\s?(.*)$")
MARK = "AGENTS_MD_CHECK_RUN"


def section(text, title):
    m = re.search(r"^## " + re.escape(title) + r"\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1) if m else None


def fence_lines(text):
    body = section(text, "Setup, run, test")
    m = re.search(r"^```(?:sh|bash|shell)\s*$(.*?)^```", body or "", re.M | re.S)
    if not m:
        return None
    out = []
    for raw in m.group(1).splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        c = COMMENT.search(s)
        out.append((s[:c.start()].strip() if c else s, (c.group(1).strip() if c else "")))
    return out


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def answers(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def serve(cmd, note, env):
    m = re.search(r"127\.0\.0\.1:(\d+)", note)
    port = int(m.group(1)) if m else None
    var = re.search(r"\(port:\s*([A-Za-z_][A-Za-z0-9_]*)\)", note)
    if var:
        port = free_port()
        env = dict(env, **{var.group(1): str(port)})
    if port is None:
        return f"`{cmd}`: the serve comment names no 127.0.0.1 address to wait for"
    log = tempfile.TemporaryFile("w+")
    # a mark in the environment of everything this line starts, so a daemon that left the
    # process group (`start -d`) is found and stopped too
    mark = f"{MARK}={os.getpid()}-{time.time_ns()}"
    env = dict(env, **{MARK: mark.split("=", 1)[1]})
    p = subprocess.Popen(cmd, shell=True, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                         start_new_session=True)
    try:
        deadline = time.time() + 90
        while time.time() < deadline:
            if answers(port):
                print(f"ok    {cmd}  (answered on 127.0.0.1:{port})")
                return None
            # the line has ended: a failure, or a start that detached (`start -d` exits 0 at
            # once); the second waits while what it started is still running
            if p.poll() is not None and (p.returncode or not [x for x in marked(mark) if not _zombie(x)]):
                break
            time.sleep(0.5)
        log.seek(0)
        return f"`{cmd}` did not answer on 127.0.0.1:{port}:\n" + log.read()[-2000:]
    finally:
        for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
            if p.poll() is None:
                try:
                    os.killpg(p.pid, sig)
                    p.wait(wait)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    pass
        stop_marked(mark)
        log.close()


def marked(mark):
    """The processes whose environment carries ``mark`` (Linux; elsewhere none)."""
    out = []
    want = mark.encode()
    for d in os.listdir("/proc") if os.path.isdir("/proc") else []:
        if not d.isdigit() or int(d) == os.getpid():
            continue
        try:
            with open(f"/proc/{d}/environ", "rb") as f:
                if want in f.read().split(b"\0"):
                    out.append(int(d))
        except OSError:
            pass
    return out


def stop_marked(mark):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        pids = marked(mark)
        for pid in pids:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        deadline = time.time() + 10
        while pids and time.time() < deadline:
            pids = [x for x in pids if os.path.exists(f"/proc/{x}")
                    and not _zombie(x)]
            time.sleep(0.2)
        if not pids:
            return


def _zombie(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return True


def run(cmd, env):
    try:
        p = subprocess.run(cmd, shell=True, cwd=ROOT, env=env, capture_output=True, text=True,
                           timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"`{cmd}` took longer than {TIMEOUT} s", ""
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0:
        return f"`{cmd}` exited {p.returncode}:\n" + out[-3000:], out
    print(f"ok    {cmd}")
    return None, out


def xpass_with_mark(output):
    bad = []
    for f in sorted(set(re.findall(r"^XPASS (\S+?\.py)::", output, re.M))):
        try:
            with open(os.path.join(ROOT, f), encoding="utf-8") as fh:
                if "xfail(" in fh.read():
                    bad.append(f"{f} now passes but still carries its open-item mark: delete the "
                               "mark block at the end of the file and its line in "
                               "tests/acceptance/README.md")
        except OSError:
            pass
    return bad


def main():
    path = os.path.join(ROOT, "AGENTS.md")
    if not os.path.isfile(path):
        sys.exit("AGENTS.md is missing")
    text = open(path, encoding="utf-8").read()
    problems = []
    lines = fence_lines(text)
    if not lines:
        sys.exit('AGENTS.md has no ```sh fence under "## Setup, run, test"')
    env = dict(os.environ)
    env["PYTEST_ADDOPTS"] = (env.get("PYTEST_ADDOPTS", "") + " -rX").strip()
    outputs, ran_acceptance = [], False
    for cmd, note in lines:
        if note.startswith("serve"):
            err = serve(cmd, note, env)
        else:
            err, out = run(cmd, env)
            outputs.append(out)
            ran_acceptance |= "pytest" in cmd and "tests/acceptance" in cmd
        if err:
            problems.append(err)
    if not ran_acceptance and os.path.isdir(os.path.join(ROOT, "tests", "acceptance")):
        err, out = run(f"{sys.executable} -m pytest tests/acceptance -q", env)
        outputs.append(out)
        if err:
            problems.append(err)
    problems += xpass_with_mark("\n".join(outputs))
    for row in re.findall(r"^\|\s*`([^`]+)`\s*\|", section(text, "Architecture in brief") or "", re.M):
        if not os.path.exists(os.path.join(ROOT, row.rstrip("/"))):
            problems.append(f"Architecture in brief names `{row}`, which does not exist")
    for p in problems:
        print("FAIL  " + p)
    print(f"{'FAILED' if problems else 'passed'}: {len(lines)} commands, {len(problems)} problems")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
