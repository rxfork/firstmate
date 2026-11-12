#!/usr/bin/env python3
"""Token-free real-Pi stop guard; interactive by default, RPC for non-TTY evidence."""

import json
import os
import pty
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

root = Path(sys.argv[1]).resolve()
rpc = len(sys.argv) > 2 and sys.argv[2] == "rpc"
core = Path(__file__).resolve().parents[2]
anchor = root / "anchor"
anchor.mkdir(parents=True)
pi = Path(shutil.which("pi")).resolve()
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
        "TMUX",
        "TMUX_PANE",
    )
}
environment.update(
    PATH=f"{pi.parent}:/usr/bin:/bin",
    HOME=str(root / "home"),
    PI_CODING_AGENT_DIR=str(root / "agent"),
    PI_SKIP_VERSION_CHECK="1",
    PI_OFFLINE="1",
    XDG_CONFIG_HOME=str(root / "config"),
    XDG_STATE_HOME=str(root / "state"),
    XDG_DATA_HOME=str(root / "data"),
    TERM="xterm-256color",
)
commands = root / "bin"
commands.mkdir()
(commands / "pi").symlink_to(pi)
environment["PATH"] = f"{commands}:{environment['PATH']}"
cli = ["bash", str(core / "bin/fm-anchor.sh")]
record = json.loads(
    subprocess.check_output(
        [*cli, "init", "--anchor", str(anchor), "--agent", "pi"],
        env=environment,
        text=True,
    )
)
environment.update(record["options"])
environment["FM_ANCHOR"] = str(anchor)
version = subprocess.check_output(
    [str(pi), "--version"], env=environment, text=True
).strip()
mode = "RPC" if rpc else "interactive"
print(f"Pi {version}: {mode} source coordination and SIGTERM shutdown")
sessions = anchor / ".firstmate/flagship/pi/primary"
sessions.mkdir(parents=True)
history = sessions / "fixture.jsonl"
stamp = datetime.now().astimezone().isoformat()
history.write_text(
    "\n".join(
        json.dumps(entry)
        for entry in (
            {
                "type": "session",
                "version": 3,
                "id": str(uuid.uuid4()),
                "timestamp": stamp,
                "cwd": str(root),
            },
            {
                "type": "custom",
                "id": "fixture1",
                "parentId": None,
                "timestamp": stamp,
                "customType": "fixture",
                "data": "durable fixture history",
            },
        )
    )
    + "\n"
)
arguments = [
    str(pi),
    "--no-extensions",
    "--no-skills",
    "--no-prompt-templates",
    "--session",
    str(history),
]
master = None
slave = None
if rpc:
    arguments.extend(["--mode", "rpc"])
else:
    master, slave = pty.openpty()
process = subprocess.Popen(
    arguments,
    cwd=root,
    env=environment,
    text=True,
    stdin=subprocess.PIPE if rpc else slave,
    stdout=subprocess.PIPE if rpc else slave,
    stderr=subprocess.PIPE if rpc else slave,
)
if slave is not None:
    os.close(slave)

    def drain():
        try:
            while os.read(master, 65536):
                pass
        except OSError:
            return

    threading.Thread(target=drain, daemon=True).start()
writer = None
unrelated = None
try:
    if rpc:
        process.stdin.write('{"type":"get_state","id":"ready"}\n')
        process.stdin.flush()
        assert select.select([process.stdout], [], [], 15)[0], (
            f"Pi {version}: RPC readiness timed out"
        )
        reply = json.loads(process.stdout.readline())
        assert reply.get("success"), f"Pi {version}: RPC readiness failed: {reply}"
    else:
        time.sleep(2)
        assert process.poll() is None, f"Pi {version}: interactive startup failed"
    state = Path(record["options"]["FM_STATE_OVERRIDE"])
    (state / ".lock").write_text(str(process.pid) + "\n")
    writer = subprocess.Popen(
        ["sleep", "120"], cwd=anchor / ".firstmate/flagship/projects", env=environment
    )
    unrelated_environment = {
        key: value for key, value in environment.items() if key not in record["options"]
    }
    unrelated = subprocess.Popen(["sleep", "120"], cwd=root, env=unrelated_environment)
    for _ in range(2):
        result = subprocess.run(
            [*cli, "stop", "--anchor", str(anchor)],
            cwd=root,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=25,
        )
        assert result.returncode == 1, (
            f"Pi {version}: unknown writer was not refused: {result.stdout} {result.stderr}"
        )
        outcome = json.loads(result.stdout)
        assert outcome["requested"], (
            f"Pi {version}: source agent was not notified: {result.stderr}"
        )
        assert any(item["pid"] == writer.pid for item in outcome["remaining"]), outcome
        assert process.poll() is None and writer.poll() is None, (
            "work was killed before coordination"
        )
    assert len(list((state / "inbox").glob("*.note"))) == 1, (
        "retry duplicated the source-agent request"
    )
    writer.terminate()
    writer.wait(timeout=5)
    result = subprocess.run(
        [*cli, "stop", "--anchor", str(anchor)],
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=25,
    )
    assert result.returncode == 0, (
        f"Pi {version}: stop failed: {result.stdout} {result.stderr}"
    )
    assert json.loads(result.stdout)["stopped"], result.stdout
    code = process.wait(timeout=5)
    if rpc:
        assert code in (0, -signal.SIGTERM, 128 + signal.SIGTERM), (
            f"Pi {version}: unexpected RPC shutdown: {code}"
        )
        print(f"RPC exit={code}; this is not interactive shutdown-hook verification")
    else:
        assert code in (0, -signal.SIGTERM, 128 + signal.SIGTERM), (
            f"Pi {version}: unexpected interactive shutdown: {code}"
        )
    assert "durable fixture history" in history.read_text(), (
        "shutdown lost existing session history"
    )
    assert unrelated.poll() is None, "source stopping touched unrelated work"
    (state / ".lock").write_text(str(unrelated.pid) + "\n")
    refused = subprocess.run(
        [*cli, "stop", "--anchor", str(anchor)],
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=25,
    )
    assert refused.returncode == 1 and unrelated.poll() is None, (
        "an unverified primary was signalled or ignored"
    )
    print(
        "Source coordination, request deduplication, history retention and process isolation passed"
    )
finally:
    for child in (writer, unrelated, process):
        if child is not None and child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
    if master is not None:
        os.close(master)
