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

# A supported service stop, not PID signaling, must resolve service refusals.
service_log = root / "service-calls"
force_failure = root / "force-failure"
services = root / "services"
services.mkdir()
service = services / "no-mistakes"
service.write_text(
    f"#!{real_python}\n"
    "import pathlib, sys\n"
    f"log = pathlib.Path({str(service_log)!r})\n"
    "with log.open('a') as output: output.write(' '.join(sys.argv[1:]) + '\\n')\n"
    f"sys.exit(0 if sys.argv[1:] == ['daemon', 'stop', '--force'] and not pathlib.Path({str(force_failure)!r}).exists() else 1)\n"
)
service.chmod(0o755)
# Both unattended and interactive calls see the same refusing service.
environment["PATH"] = f"{services}:{environment['PATH']}"
interactive["PATH"] = f"{services}:{interactive['PATH']}"
review_record = Path(record["options"]["NM_HOME"]) / "preserved-review"
review_record.parent.mkdir(parents=True, exist_ok=True)
review_record.write_text("review history")
for answers, tty, succeeds in (
    ("y\n", False, False),
    ("", True, False),
    ("\n", True, False),
    ("n\n", True, False),
    ("y\n", True, True),
):
    service_log.unlink(missing_ok=True)
    result = stop(answers, tty)
    assert (result.returncode == 0) == succeeds, result.stderr + result.stdout
    calls = service_log.read_text().splitlines()
    assert ("daemon stop --force" in calls) == succeeds, calls
    assert ("Force-stop no-mistakes" in result.stderr) == tty, result.stderr
    assert review_record.read_text() == "review history"
    assert json.loads((anchor / ".firstmate/flagship.json").read_text()) == record

force_failure.touch()
result = stop("y\n")
assert result.returncode != 0 and json.loads(result.stdout)["errors"], result
force_failure.unlink()

process = worker(ignore_term=True)
try:
    service_log.unlink(missing_ok=True)
    result = stop("y\n\ny\n")
    assert result.returncode == 0, result.stderr + result.stdout
    process.wait(timeout=2)
    calls = service_log.read_text().splitlines()
    assert calls == ["daemon stop", *["daemon stop --force"] * 3], calls
finally:
    if process.poll() is None:
        process.kill()
    process.wait()
print("review force-stop is explicit, survives signal escalation, and preserves records")
