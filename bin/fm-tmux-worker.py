"""Record, verify, and stop a vessel worker's Linux terminal session."""

import json
import os
import re
import signal
import stat
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

PROC = Path("/proc")


def scope():
    return (
        (PROC / "sys/kernel/random/boot_id").read_text().strip(),
        str((PROC / "self/ns/pid").stat().st_ino),
    )


def process(pid):
    path = PROC / str(pid) / "stat"
    try:
        fields = path.read_text(errors="replace").rpartition(") ")[2].split()
    except (FileNotFoundError, ProcessLookupError):
        return None
    if fields[0] in {"Z", "X"}:
        return None
    return int(fields[3]), fields[19]


def members(session):
    result = {}
    for entry in PROC.iterdir():
        if not entry.name.isdecimal():
            continue
        pid = int(entry.name)
        identity = process(pid)
        if identity and identity[0] == session:
            result[pid] = identity[1]
    return result


def audit_file(path, mode):
    flags = os.O_NOFOLLOW | os.O_NONBLOCK
    flags |= os.O_RDONLY if mode == "r" else os.O_WRONLY | os.O_APPEND | os.O_CREAT
    stream = os.fdopen(os.open(path, flags, 0o600), mode)
    info = os.fstat(stream.fileno())
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        stream.close()
        raise ValueError("worker recovery audit must be a private regular file")
    return stream


def audit(path, event, task, endpoint, worktree, **fields):
    row = dict(
        when=datetime.now(UTC).isoformat(),
        uid=os.getuid(),
        event=event,
        task=task,
        endpoint=endpoint,
        worktree=worktree,
        **fields,
    )
    with audit_file(path, "a") as stream:
        stream.write(json.dumps(row) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def stop(session, known, log, terminal):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        current = members(session)
        if not current:
            break
        witnesses = {
            pid: start for pid, start in known.items() if current.get(pid) == start
        }
        if not witnesses:
            raise ValueError(
                f"cannot verify who owns terminal session {session}; no process was signaled in this pass"
            )
        # Pin each recipient, then verify its start and membership again before signaling.
        pinned = {}
        try:
            targets = {}
            for pid, start in current.items():
                try:
                    fd = os.pidfd_open(pid)
                except ProcessLookupError:
                    continue
                pinned[pid] = fd
                if process(pid) != (session, start):
                    continue
                try:
                    if (PROC / str(pid)).stat().st_uid != os.getuid():
                        raise ValueError(
                            f"terminal process {pid} has a different owner"
                        )
                except (FileNotFoundError, ProcessLookupError):
                    continue
                targets[pid] = fd
            if not any(
                process(pid) == (session, start) for pid, start in witnesses.items()
            ):
                if not members(session):
                    break
                raise ValueError(
                    f"terminal session {session} changed during inspection"
                )
            known.update({pid: current[pid] for pid in targets})
            log(
                sig.name,
                terminal=terminal,
                processes={pid: current[pid] for pid in targets},
            )
            for pid, fd in targets.items():
                if process(pid) == (session, current[pid]):
                    try:
                        signal.pidfd_send_signal(fd, sig)
                    except ProcessLookupError:
                        pass
        finally:
            for fd in pinned.values():
                os.close(fd)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and members(session):
            time.sleep(0.05)
    if members(session) or members(session):
        raise ValueError(f"terminal session {session} did not stop")
    log("verified-stopped", terminal=terminal)


def main():
    action, value, *args = sys.argv[1:]
    if action == "attest":
        task, endpoint, worktree, evidence = args
        audit(
            value,
            "operator-attested-stopped",
            task,
            endpoint,
            worktree,
            evidence=evidence,
        )
        return
    boot, namespace = scope()
    if action == "record":
        if not re.fullmatch(r"[1-9][0-9]*", value):
            raise ValueError("tmux did not report one pane process")
        pid = int(value)
        identity = process(pid)
        if not identity or identity[0] != pid:
            raise ValueError("tmux pane process is not a live terminal session leader")
        print(f"{boot}:{namespace}:{pid}:{identity[1]}")
        return
    match = re.fullmatch(r"([^:]+):([0-9]+):([1-9][0-9]*)(?::([0-9]+))?", value)
    if not match or match.group(1, 2) != (boot, namespace):
        raise ValueError("original boot or process namespace cannot be verified")
    session = int(match[3])
    if action == "stop":
        path, task, endpoint, worktree = args

        def log(event, **fields):
            audit(path, event, task, endpoint, worktree, **fields)

        known = {session: match[4]} if match[4] else {}
        if known:
            # A prior stop may have killed the leader and been interrupted.
            # Its audit authorizes only exact PID/start pairs in this terminal.
            try:
                with audit_file(path, "r") as stream:
                    for line in stream:
                        row = json.loads(line)
                        if (
                            row.get("task"),
                            row.get("endpoint"),
                            row.get("worktree"),
                            row.get("terminal"),
                        ) != (task, endpoint, worktree, value):
                            continue
                        if row["event"] == "verified-stopped":
                            known = {session: match[4]}
                        elif row["event"] in {"SIGTERM", "SIGKILL"}:
                            known.update(
                                {
                                    int(pid): start
                                    for pid, start in row["processes"].items()
                                }
                            )
            except FileNotFoundError:
                pass
        stop(session, known, log, value)
        return
    if action not in ("gone", "idle"):
        raise ValueError("unknown terminal operation")
    for _ in range(2):
        for pid in members(session):
            if action == "idle" and pid == session:
                continue
            raise ValueError(
                f"original terminal session {session} still has a live process (PID {pid})"
            )


if __name__ == "__main__":
    try:
        main()
    except (
        ValueError,
        OSError,
        IndexError,
        KeyError,
        AttributeError,
        TypeError,
    ) as error:
        sys.exit(str(error))
