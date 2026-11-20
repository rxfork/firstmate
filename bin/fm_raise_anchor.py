"""Vessel shutdown for fm-raise-anchor.sh and sailing.

Stop registered workers/services first, preserving repositories and task state.
Only a terminal can authorize escalation for the displayed same-user, scoped
process identities: SIGTERM defaults yes; SIGKILL defaults no. Each approval
covers only those PID/start-time pairs, never a process name or process group.
Noninteractive peers accept explicit identity-bound approvals from the caller.
Stable pidfds prevent PID reuse between verification and signaling.
"""

import hashlib
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


def process_info(pid):
    text = (Path("/proc") / str(pid) / "stat").read_text(errors="replace")
    name, _, tail = text.rpartition(") ")
    fields = tail.split()
    return {
        "pid": int(pid),
        "name": name.partition("(")[2],
        "state": fields[0],
        "ppid": int(fields[1]),
        "start": fields[19],
    }


def ancestors(pid):
    result = set()
    while pid > 0 and pid not in result:
        result.add(pid)
        try:
            pid = process_info(pid)["ppid"]
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            break
    return result


def session_pid(record):
    lock = Path(record["options"]["FM_STATE_OVERRIDE"]) / ".lock"
    if not lock.exists():
        return ""
    if lock.is_symlink() or not lock.is_file():
        raise ValueError("primary session lock must be a regular file")
    return lock.read_text().partition("\n")[0]


def vessel_processes(anchor, record):
    vessel = Path(record["options"]["FM_HOME"])
    actors = ancestors(os.getpid())
    pid = session_pid(record)
    if pid.isdigit():
        actors.discard(int(pid))
    result = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit() or int(path.name) in actors:
            continue
        try:
            if path.stat().st_uid != os.getuid():
                if path.name == pid:
                    raise ValueError(
                        "recorded primary belongs to another user; cannot verify shutdown"
                    )
                continue
            info = process_info(int(path.name))
            if info["state"] in ("Z", "X"):
                continue
            try:
                cwd = os.readlink(path / "cwd")
            except PermissionError:
                cwd = ""
            try:
                environment = dict(
                    entry.split(b"=", 1)
                    for entry in (path / "environ").read_bytes().split(b"\0")
                    if b"=" in entry
                )
            except PermissionError:
                environment = {}
            scoped = Path(cwd).is_relative_to(vessel) or any(
                Path(os.fsdecode(environment.get(name.encode(), b""))).is_relative_to(
                    vessel
                )
                for name in record["options"]
            )
            if scoped or path.name == pid:
                result.append({**info, "cwd": cwd, "scoped": scoped})
        except (FileNotFoundError, ProcessLookupError):
            continue
    return result


def primary_process(record, processes):
    pid = session_pid(record)
    for process in processes:
        if str(process["pid"]) != pid or not process["scoped"]:
            continue
        # The session-lock owner also recognizes Pi's exact kernel process name.
        if process["name"] == "pi":
            return process
        executable = shutil.which("pi")
        if executable is None:
            return None
        arguments = (Path("/proc") / pid / "cmdline").read_bytes().split(b"\0")
        if (Path("/proc") / pid / "exe").resolve() == Path(executable).resolve() or any(
            value
            and Path(os.fsdecode(value)).is_absolute()
            and Path(os.fsdecode(value)).resolve() == Path(executable).resolve()
            for value in arguments[:2]
        ):
            return process
    return None


def with_stop_confirmation(stop):
    """Coordinate local or SSH stopping; approval never reads protocol stdin."""
    result = stop(None)
    try:
        interactive = os.isatty(3)
    except OSError:
        interactive = False
    if result["stopped"] or not interactive:
        return result
    confirmed = None
    force_review_stop = False
    with os.fdopen(os.dup(3)) as terminal:
        if result.get("review_stop_refused"):
            print(
                "no-mistakes refused to stop. Force-stopping may abandon active reviews; "
                "repository files and review records will not be deleted.\n"
                "Force-stop no-mistakes for this vessel? [y/N] ",
                end="",
                file=sys.stderr,
                flush=True,
            )
            if terminal.readline().strip().lower() not in ("y", "yes"):
                return result
            force_review_stop = True
            result = stop(
                {"signal": "SIGTERM", "processes": [], "force_review_stop": True}
            )
            if result["stopped"] or result.get("review_stop_refused"):
                return result
        for name, default in (("SIGTERM", True), ("SIGKILL", False)):
            processes = [
                item
                for item in result["remaining"]
                if item["scoped"]
                and (confirmed is None or (item["pid"], item["start"]) in confirmed)
            ]
            if not processes:
                break
            print("fm-raise-anchor: processes still running:", file=sys.stderr)
            for item in processes:
                print(
                    f"  PID {item['pid']}  {item['name']!r}  {item['cwd']!r}",
                    file=sys.stderr,
                )
            if name == "SIGKILL":
                print("Force-killing can interrupt writes.", file=sys.stderr)
            print(
                f"Send {name} to these processes? {'[Y/n]' if default else '[y/N]'} ",
                end="",
                file=sys.stderr,
                flush=True,
            )
            answer = terminal.readline()
            if not answer:
                break
            answer = answer.strip().lower()
            if answer not in ("y", "yes") and not (default and answer == ""):
                break
            confirmed = {(item["pid"], item["start"]) for item in processes}
            approval = {"signal": name, "processes": processes}
            if force_review_stop:
                approval["force_review_stop"] = True
            result = stop(approval)
            if result["stopped"]:
                break
    return result


def validate_approval(approved):
    if (
        not isinstance(approved, dict)
        or set(approved) - {"force_review_stop"} != {"signal", "processes"}
        or (
            "force_review_stop" in approved
            and approved["force_review_stop"] is not True
        )
        or approved["signal"] not in ("SIGTERM", "SIGKILL")
        or not isinstance(approved["processes"], list)
    ):
        raise ValueError("invalid process stop approval")


def signal_processes(record, processes, approved):
    """Signal only approved identities still in the current unmanaged inventory."""
    validate_approval(approved)
    current = {item["pid"]: item for item in processes}
    handles = []
    try:
        for expected in approved["processes"]:
            item = current.get(expected["pid"])
            if item is None:
                continue
            if not item["scoped"] or str(item["pid"]) == session_pid(record):
                raise ValueError(
                    "cannot signal an unverified primary or unscoped process"
                )
            if item["start"] != expected["start"]:
                raise ValueError(
                    "process identity changed before signaling; retry stopping"
                )
            try:
                fd = os.pidfd_open(item["pid"])
                handles.append(fd)
                if process_info(item["pid"])["start"] != expected["start"]:
                    raise ValueError(
                        "process identity changed before signaling; retry stopping"
                    )
                signal.pidfd_send_signal(fd, getattr(signal, approved["signal"]))
            except (FileNotFoundError, ProcessLookupError):
                continue
        deadline = time.monotonic() + 5
        pending = set(handles)
        while pending and time.monotonic() < deadline:
            ready, _, _ = select.select(
                list(pending), [], [], max(0, deadline - time.monotonic())
            )
            pending.difference_update(ready)
    finally:
        for fd in handles:
            os.close(fd)


def vessel_environment(anchor, record):
    environment = {
        **os.environ,
        **record["options"],
        "FM_ANCHOR": str(anchor),
        "FM_BACKEND": "tmux",
    }
    for name in (
        "TMUX",
        "TMUX_PANE",
        "FM_ROOT_OVERRIDE",
        "FM_VESSEL_RELAUNCH_TX",
        "FM_VESSEL_RECOVERY_FD",
    ):
        environment.pop(name, None)
    return environment


def stop_vessel(anchor, record, approved=None, recovery=None, lock_fd=None):
    if approved is not None:
        validate_approval(approved)
    options = record["options"]
    environment = vessel_environment(anchor, record)
    scripts = Path(__file__).resolve().parent
    errors = []

    def stop(command, lifecycle=False):
        env = environment
        if lifecycle and lock_fd is not None:
            env = {**env, "FM_VESSEL_RECOVERY_FD": str(lock_fd)}
        result = subprocess.run(
            command,
            env=env,
            stdout=sys.stderr,
            check=False,
            pass_fds=(lock_fd,) if lifecycle and lock_fd is not None else (),
        )
        if result.returncode:
            errors.append(
                f"{Path(command[0]).name} refused to stop (exit {result.returncode})"
            )

    socket = Path(options["TMUX_TMPDIR"]) / f"tmux-{os.getuid()}" / "default"
    shells = set()
    if recovery is not None:
        recovery.stop_workers()
    if socket.is_socket() and recovery is None:
        windows = subprocess.check_output(
            [
                "tmux",
                "-S",
                str(socket),
                "list-windows",
                "-a",
                "-F",
                "#{session_name}:#{window_name}",
            ],
            env=environment,
            text=True,
        ).splitlines()
        for path in Path(options["FM_STATE_OVERRIDE"]).glob("*.meta"):
            metadata = dict(
                line.split("=", 1)
                for line in path.read_text().splitlines()
                if "=" in line
            )
            if metadata.get("window") in windows:
                stop(
                    [str(scripts / "fm-control.sh"), path.stem, "exit"], lifecycle=True
                )
    if socket.is_socket():
        panes = subprocess.check_output(
            [
                "tmux",
                "-S",
                str(socket),
                "list-panes",
                "-a",
                "-F",
                "#{pid}\t#{pane_pid}\t#{pane_current_command}",
            ],
            env=environment,
            text=True,
        ).splitlines()
        for pane in panes:
            server, pid, command = pane.split("\t")
            shells.add(int(server))
            if command in ("bash", "fish", "zsh", "sh", "dash"):
                shells.add(int(pid))
    review_stop_refused = False
    if shutil.which("no-mistakes"):
        command = ["no-mistakes", "daemon", "stop"]
        if approved is not None and approved.get("force_review_stop") is True:
            command.append("--force")
        previous_errors = len(errors)
        stop(command)
        review_stop_refused = len(errors) > previous_errors
    processes = vessel_processes(anchor, record)
    primary = primary_process(record, processes)
    primary_actors = ancestors(primary["pid"]) if primary else set()
    owned = {primary["pid"]} if primary else set()
    for item in processes:
        try:
            arguments = (
                (Path("/proc") / str(item["pid"]) / "cmdline").read_bytes().split(b"\0")
            )
        except (FileNotFoundError, ProcessLookupError):
            continue
        if any(
            argument
            in (
                os.fsencode(scripts / "fm-watch.sh"),
                os.fsencode(scripts / "fm-watch-arm.sh"),
            )
            for argument in arguments[:2]
        ):
            owned.add(item["pid"])
    remaining = [
        item
        for item in processes
        if item["pid"] not in shells | primary_actors
        and not (ancestors(item["pid"]) & owned)
    ]
    if approved is not None:
        signal_processes(record, remaining, approved)
        live = {
            (item["pid"], item["start"]) for item in vessel_processes(anchor, record)
        }
        remaining = [item for item in remaining if (item["pid"], item["start"]) in live]
    requested = False
    if remaining or errors:
        if primary:
            message = (
                "A local vessel operation needs work to stop. "
                "Stop the remaining vessel processes using their own stop commands; preserve "
                "all repositories, worktrees, dirty files and durable task records. Do not "
                "start new work or discard anything. Retrying the operation can request Pi shutdown "
                "once the other work has stopped. Remaining processes: "
                + json.dumps(remaining)
                + "; stop errors: "
                + json.dumps(errors)
            )
            generation = f"{record['harbor']}:{Path('/proc/sys/kernel/random/boot_id').read_text().strip()}:{primary['pid']}:{primary['start']}"
            request_id = (
                "vessel-stop-" + hashlib.sha256(generation.encode()).hexdigest()[:24]
            )
            result = subprocess.run(
                [
                    str(scripts / "fm-inbox.sh"),
                    "note",
                    "--request-id",
                    request_id,
                    "--",
                    message,
                ],
                env=environment,
                stdout=sys.stderr,
                check=False,
            )
            requested = result.returncode == 0
            if not requested:
                errors.append(
                    "could not notify the source primary; no transfer was started"
                )
    else:
        if primary:
            fd = os.pidfd_open(primary["pid"])
            try:
                if process_info(primary["pid"])["start"] != primary["start"]:
                    raise ValueError("primary changed before shutdown; retry sailing")
                signal.pidfd_send_signal(fd, signal.SIGTERM)
                if not select.select([fd], [], [], 10)[0]:
                    errors.append(
                        "Pi did not finish graceful shutdown within 10 seconds"
                    )
            finally:
                os.close(fd)
        stop([str(scripts / "fm-watch-arm.sh"), "--stop"])
        if socket.is_socket() and not errors:
            if recovery is not None:
                recovery.before_server_stop()
            stop(["tmux", "-S", str(socket), "kill-server"])
        # Pi's sandbox wrapper can take a moment to finish its own cleanup.
        deadline = time.monotonic() + 5
        while True:
            remaining = vessel_processes(anchor, record)
            if not remaining or time.monotonic() >= deadline:
                break
            time.sleep(0.1)
    return {
        "stopped": not remaining and not errors,
        "remaining": remaining,
        "errors": errors,
        "requested": requested,
        "review_stop_refused": review_stop_refused,
    }
