#!/usr/bin/env python3
"""Exercise real stopping with disposable processes and a simulated terminal FD.

The sandbox may deny PTY allocation, so only isatty(3) is simulated; the CLI,
stdin forwarding, prompts, process inventory, signals and waits are real.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

core = Path(__file__).resolve().parents[2]
root = Path(sys.argv[1])
root.mkdir(parents=True)
real_python = sys.executable
environment = {
    key: value
    for key, value in os.environ.items()
    if not key.startswith("FM_")
    and key
    not in (
        "TREEHOUSE_ROOT",
        "NM_HOME",
        "LAVISH_AXI_STATE_DIR",
        "PI_CODING_AGENT_SESSION_DIR",
        "TMUX_TMPDIR",
        "XDG_CACHE_HOME",
    )
}
# Invoke the public launcher, but replace terminal capability detection only.
commands = root / "bin"
commands.mkdir()
python = commands / "python3"
python.write_text(
    f"#!{real_python}\n"
    "import os, runpy, sys\n"
    "if sys.argv[1].endswith('/fm-vessel.py'):\n"
    "    real_isatty = os.isatty\n"
    "    os.isatty = lambda fd: fd == 3 or real_isatty(fd)\n"
    "    sys.path.insert(0, os.path.dirname(sys.argv[1]))\n"
    "    sys.argv = sys.argv[1:]\n"
    "    runpy.run_path(sys.argv[0], run_name='__main__')\n"
    "else:\n"
    f"    os.execv({real_python!r}, [{real_python!r}, *sys.argv[1:]])\n"
)
python.chmod(0o755)
if len(sys.argv) > 2:
    sys.exit(
        subprocess.run(
            sys.argv[2:],
            env={**os.environ, "PATH": f"{commands}:{os.environ['PATH']}"},
            check=False,
        ).returncode
    )
interactive = {**environment, "PATH": f"{commands}:{environment['PATH']}"}
anchor = root / "anchor"
anchor.mkdir()
cli = ["bash", str(core / "bin/fm-anchor.sh")]
record = json.loads(
    subprocess.check_output(
        [*cli, "init", "--anchor", str(anchor), "--agent", "pi"],
        env=environment,
        text=True,
    )
)
stop_cli = ["bash", str(core / "bin/fm-raise-anchor.sh")]


def worker(ignore_term=False):
    ready = Path(record["options"]["FM_HOME"]) / "ready"
    ready.unlink(missing_ok=True)
    process = subprocess.Popen(
        [
            real_python,
            "-c",
            "import pathlib,signal,time; "
            + ("signal.signal(signal.SIGTERM, signal.SIG_IGN); " if ignore_term else "")
            + "pathlib.Path('ready').write_text('ready'); time.sleep(120)",
        ],
        cwd=Path(record["options"]["FM_HOME"]),
        env=environment,
    )
    for _ in range(200):
        if ready.exists():
            ready.unlink()
            return process
        assert process.poll() is None, "fixture process exited early"
        time.sleep(0.01)
    process.kill()
    process.wait()
    raise AssertionError("fixture process did not start")


def stop(answers, tty=True):
    return subprocess.run(
        [*stop_cli, "--anchor", str(anchor)],
        input=answers,
        text=True,
        env=interactive if tty else environment,
        capture_output=True,
        timeout=30,
        check=False,
    )


for answers, tty, ignore_term, stopped in (
    ("\n", True, False, True),
    ("n\n", True, False, False),
    ("", True, False, False),
    ("y\ny\n", False, True, False),
    ("\n\n", True, True, False),
    ("\n", True, True, False),
    ("\ny\n", True, True, True),
):
    process = worker(ignore_term)
    unrelated = subprocess.Popen(
        [real_python, "-c", "import time; time.sleep(120)"],
        cwd=root,
        env=environment,
    )
    try:
        result = stop(answers, tty)
        if stopped:
            assert result.returncode == 0, result.stderr + result.stdout
            process.wait(timeout=2)
            assert json.loads(result.stdout)["stopped"] is True
        else:
            assert result.returncode != 0, result.stderr + result.stdout
            assert process.poll() is None, "declined/unattended stop killed work"
        if tty and answers:
            assert "SIGTERM" in result.stderr and "[Y/n]" in result.stderr, (
                result.stderr
            )
            assert str(process.pid) in result.stderr, result.stderr
        if tty and ignore_term:
            assert "SIGKILL" in result.stderr and "[y/N]" in result.stderr, (
                result.stderr
            )
        if not tty:
            assert "[Y/n]" not in result.stderr, result.stderr
        assert unrelated.poll() is None, (
            "stopping killed an unrelated same-name process"
        )
    finally:
        for child in (process, unrelated):
            if child.poll() is None:
                child.kill()
            child.wait()
    assert (anchor / ".firstmate/flagship.json").exists()
    assert json.loads((anchor / ".firstmate/flagship.json").read_text()) == record
result = stop("", tty=False)
assert result.returncode == 0 and json.loads(result.stdout)["stopped"], result.stderr
print(
    "confirmed TERM defaults yes, KILL defaults no; refusal/EOF/non-TTY preserve work"
)
