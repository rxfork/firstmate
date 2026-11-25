"""Record or check a local vessel worker's Linux terminal session.

Only compares identities in the boot and PID namespace where they were read.
Detached services that intentionally leave the terminal session have their own
lifecycle; this only covers the tmux worker and its terminal-bound children.
"""

import re
import sys
from pathlib import Path

PROC = Path("/proc")


def scope():
    return (
        (PROC / "sys/kernel/random/boot_id").read_text().strip(),
        str((PROC / "self/ns/pid").stat().st_ino),
    )


def process_session(path):
    fields = path.read_text(errors="replace").rpartition(") ")[2].split()
    return fields[0], fields[3]


def main():
    action, value = sys.argv[1:]
    boot, namespace = scope()
    if action == "record":
        if not re.fullmatch(r"[1-9][0-9]*", value):
            raise ValueError("tmux did not report one pane process")
        state, session = process_session(PROC / value / "stat")
        if session != value or state in {"Z", "X"}:
            raise ValueError("tmux pane process is not a live terminal session leader")
        print(f"{boot}:{namespace}:{session}")
        return
    if action != "gone":
        raise ValueError("expected record or gone")
    recorded_boot, recorded_namespace, session = value.split(":")
    if (recorded_boot, recorded_namespace) != (boot, namespace) or not re.fullmatch(
        r"[1-9][0-9]*", session
    ):
        raise ValueError("original boot or process namespace cannot be verified")
    # Scan again if group members disappeared during iteration. An unreadable
    # process table is not evidence of absence.
    for _ in range(2):
        for proc in PROC.iterdir():
            if not proc.name.isdecimal():
                continue
            try:
                state, sid = process_session(proc / "stat")
            except (FileNotFoundError, ProcessLookupError):
                continue
            if sid == session and state not in {"Z", "X"}:
                raise ValueError(
                    f"original terminal session {session} still has a live process (PID {proc.name})"
                )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, IndexError) as error:
        sys.exit(str(error))
