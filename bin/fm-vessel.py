#!/usr/bin/env python3
"""Private metadata and sailing implementation for fm-anchor.sh.

Sailing stops source work before copying at identical physical anchor paths.
Host tools/authentication are not copied. Ordinary SSH configuration is used.

Record owner: version 1 contains the original local anchor configuration;
version 2 adds a stable vessel_id, last_known_harbor and known_harbors (SSH
names). harbor still identifies the host owning this particular local record.
Host components are lowercase and known_harbors is deduplicated; SSH user name
case is preserved. check/run normalize in memory without rewriting; explicit
sail persists canonical names and upgrades version 1.

Peer inspect reads a record and tests payload availability. prepare compares
that exact record under the anchor lock, marks it inactive and reserves it for
sailing_to. The receiving record reserves itself with the same field, keeping
its inactive cache unavailable as a source. Only that destination may retry.
After rsync, commit compares the
prepared record, clears sailing_to and updates the origin's last-known hint.
Rsync incrementally mirrors into the same receiver directory with deletions;
inactive local state is disposable cache, not a backup or an anchored source.
The destination activates only after acknowledgement and a complete sync.
Interrupted transfers remain inactive and retry against that cache. There are
no transfer receipts, leases, global registry or generic process manager.

Stopping uses recorded workers' fm-control exit, the home-scoped watcher stop,
and no-mistakes daemon stop without force. A verified vessel-local primary Pi
receives SIGTERM through a pidfd; its owned children and private tmux server
must finish before copy. Unfamiliar processes and refused stops are reported
through the existing primary inbox, once per primary process generation.
prepare releases its lock between destination retries, allowing the primary
to finish cleanup. Sailing waits up to 60 seconds for that coordination before
refusing without a copy. The source remains reserved for the same retry.

status lists accessible same-user /proc cwd/environment matches against vessel
paths, plus the recorded primary. This is not certification of every possible
writer: unrelated protected processes are not inspected, and a daemon that
clears its environment and leaves the vessel may not be attributable here.
Known but unverified primary ownership or remaining processes prevent transfer.
Only Pi is signalled directly; there is no name-pattern kill, force stop,
teardown, worktree return, or cleanup of unlanded work.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import select
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

BASE_FIELDS = {"version", "anchored", "anchor", "harbor", "backend", "options"}
ROUTE_FIELDS = {"vessel_id", "last_known_harbor", "known_harbors", "sailing_to"}
OPTION_NAMES = {
    "FM_HOME",
    "FM_CONFIG_OVERRIDE",
    "FM_DATA_OVERRIDE",
    "FM_STATE_OVERRIDE",
    "FM_PROJECTS_OVERRIDE",
    "TREEHOUSE_ROOT",
    "NM_HOME",
    "LAVISH_AXI_STATE_DIR",
    "PI_CODING_AGENT_SESSION_DIR",
    "TMUX_TMPDIR",
    "XDG_CACHE_HOME",
}
SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]


def fail(message):
    raise ValueError(message)


def harbor_name(value):
    if not isinstance(value, str):
        fail("harbor must be an SSH host name or configured alias")
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]*", value):
        fail("harbor must be an SSH host name or configured alias")
    user, separator, host = value.rpartition("@")
    return user + separator + host.lower() if separator else value.lower()


def here():
    return harbor_name(subprocess.check_output(["uname", "-n"], text=True).strip())


def anchor_path(value):
    anchor = Path(value).resolve(strict=True)
    if not anchor.is_dir():
        fail("anchor must be an existing directory")
    for path in (anchor / ".firstmate", anchor / ".firstmate.json"):
        if path.is_symlink():
            fail("vessel and metadata must not be symlinks")
    return anchor


def validate_record(record, anchor):
    if not isinstance(record, dict):
        fail("invalid vessel record")
    version = record.get("version")
    fields = (
        BASE_FIELDS if version == 1 else BASE_FIELDS | (ROUTE_FIELDS - {"sailing_to"})
    )
    if version == 2 and "sailing_to" in record:
        fields |= {"sailing_to"}
    if type(version) is not int or version not in (1, 2) or set(record) != fields:
        fail("unsupported vessel record")
    if record["anchor"] != str(anchor) or type(record["anchored"]) is not bool:
        fail("anchor path differs; sailing requires identical physical paths")
    record["harbor"] = harbor_name(record["harbor"])
    if (
        record["backend"] != "tmux"
        or not isinstance(record["options"], dict)
        or set(record["options"]) != OPTION_NAMES
    ):
        fail("invalid vessel configuration")
    vessel = anchor / ".firstmate"
    for value in record["options"].values():
        if (
            not isinstance(value, str)
            or not Path(value).is_absolute()
            or not Path(value).resolve().is_relative_to(vessel)
        ):
            fail("selected directories must remain inside the vessel")
    for name, directory in (
        ("FM_HOME", vessel),
        ("TMUX_TMPDIR", vessel / "tmux"),
        ("XDG_CACHE_HOME", vessel / "cache"),
    ):
        if record["options"][name] != str(directory):
            fail(f"{name} does not match the vessel layout")
    if version == 2:
        if (
            not isinstance(record["vessel_id"], str)
            or str(uuid.UUID(record["vessel_id"])) != record["vessel_id"]
        ):
            fail("invalid vessel identity")
        record["last_known_harbor"] = harbor_name(record["last_known_harbor"])
        known = record["known_harbors"]
        if (
            not isinstance(known, list)
            or not known
            or any(not isinstance(name, str) for name in known)
        ):
            fail("invalid known harbors")
        known = list(dict.fromkeys(harbor_name(name) for name in known))
        record["known_harbors"] = known
        if not {
            record["harbor"],
            record["last_known_harbor"],
        }.issubset(known):
            fail("invalid known harbors")
        if "sailing_to" in record:
            record["sailing_to"] = harbor_name(record["sailing_to"])
            if record["anchored"] or record["sailing_to"] not in known:
                fail("invalid sailing destination")
    return record


def read_record(anchor):
    path = anchor / ".firstmate.json"
    if path.is_symlink() or not path.is_file():
        fail("vessel record must be a regular file")
    with path.open() as stream:
        return validate_record(json.load(stream), anchor)


def base_record(record):
    return {
        **{key: value for key, value in record.items() if key not in ROUTE_FIELDS},
        "version": 1,
    }


def with_routing(record):
    if record["version"] == 2:
        return dict(record)
    return {
        **record,
        "version": 2,
        "vessel_id": str(uuid.uuid4()),
        "last_known_harbor": record["harbor"],
        "known_harbors": [record["harbor"]],
    }


def write_record(anchor, record):
    validate_record(record, anchor)
    if (anchor / ".firstmate.json").is_symlink():
        fail("vessel record must not be a symlink")
    fd, name = tempfile.mkstemp(prefix=".firstmate.json.", dir=anchor)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(record, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, anchor / ".firstmate.json")
        directory = os.open(anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def anchor_lock(anchor):
    path = anchor / ".firstmate.lock"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            fail("anchor lock must be a regular file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail("another anchor operation is in progress")
        yield
    finally:
        os.close(fd)


def known_harbors(record):
    return record.get("known_harbors", [record["harbor"]])


def merge_harbors(*groups):
    return list(dict.fromkeys(harbor_name(name) for group in groups for name in group))


def check_payload(anchor, record, payload=None):
    vessel = anchor / ".firstmate"
    payload = payload or vessel
    if not payload.is_dir() or payload.is_symlink():
        fail("vessel payload must be a directory")
    for name, value in record["options"].items():
        if name == "TMUX_TMPDIR":
            continue
        directory = payload / Path(value).relative_to(vessel)
        if not directory.is_dir() or not directory.resolve().is_relative_to(payload):
            fail(f"missing or outside-vessel directory: {name}")
    backend = (
        payload
        / Path(record["options"]["FM_CONFIG_OVERRIDE"]).relative_to(vessel)
        / "backend"
    )
    if (
        not backend.is_file()
        or not backend.resolve().is_relative_to(payload)
        or backend.read_bytes().rstrip(b"\n") != b"tmux"
    ):
        fail("vessel backend configuration changed")


def provision_projects(anchor, record):
    projects = Path(record["options"]["FM_PROJECTS_OVERRIDE"])
    for directory, directories, _files in os.walk(
        anchor, onerror=lambda error: fail(str(error))
    ):
        source = Path(directory)
        is_repo = (source / ".git").exists()
        directories[:] = (
            []
            if is_repo
            else [
                name
                for name in directories
                if name != ".firstmate" and not (source / name).is_symlink()
            ]
        )
        if not is_repo:
            continue
        relative = source.relative_to(anchor) if source != anchor else Path(anchor.name)
        destination = projects / relative
        if destination.is_symlink() or not destination.resolve().is_relative_to(
            projects
        ):
            fail(f"project destination leaves the vessel: {destination}")
        if destination.exists():
            if (
                not (destination / ".git").is_dir()
                or (destination / ".git").is_symlink()
            ):
                fail(f"existing project is not an independent clone: {destination}")
            continue
        origin = subprocess.run(
            ["git", "-C", str(source), "remote", "get-url", "origin"],
            check=False,
            text=True,
            capture_output=True,
        )
        if origin.returncode or not origin.stdout.strip():
            fail(f"{source}: cannot read origin: {origin.stderr.strip()}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        print(f"fm-anchor: cloning {relative} into the vessel", file=sys.stderr)
        subprocess.run(
            [
                "git",
                "clone",
                "--no-local",
                "--",
                origin.stdout.strip(),
                str(destination),
            ],
            cwd=source,
            stdout=sys.stderr,
            check=True,
        )


def remote(anchor, host, operation, request=None):
    host = harbor_name(host)
    script = 'exec "${XDG_DATA_HOME:-$HOME/.local/share}/firstmate/bin/fm-anchor.sh" _peer --anchor "$1" --op "$2"'
    command = shlex.join(["sh", "-c", script, "sh", str(anchor), operation])
    try:
        result = subprocess.run(
            ["ssh", *SSH_OPTIONS, "--", host, command],
            input=json.dumps(request) if request is not None else "",
            text=True,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or error.stdout.strip() or "no diagnostic output"
        fail(f"{host} {operation} failed (exit {error.returncode}): {detail}")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    reply = json.loads(result.stdout)
    reply["here"] = harbor_name(reply["here"])
    if reply["record"] is not None:
        validate_record(reply["record"], anchor)
    return reply


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
        fail("primary session lock must be a regular file")
    return lock.read_text().partition("\n")[0]


def vessel_processes(anchor, record):
    vessel = anchor / ".firstmate"
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
                    fail(
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
                for name in OPTION_NAMES
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


def stop_vessel(anchor, record):
    options = record["options"]
    environment = {
        **os.environ,
        **options,
        "FM_ANCHOR": str(anchor),
        "FM_BACKEND": "tmux",
    }
    for name in ("TMUX", "TMUX_PANE", "FM_ROOT_OVERRIDE"):
        environment.pop(name, None)
    scripts = Path(__file__).resolve().parent
    errors = []

    def stop(command):
        result = subprocess.run(
            command, env=environment, stdout=sys.stderr, check=False
        )
        if result.returncode:
            errors.append(
                f"{Path(command[0]).name} refused to stop (exit {result.returncode})"
            )

    socket = Path(options["TMUX_TMPDIR"]) / f"tmux-{os.getuid()}" / "default"
    shells = set()
    if socket.is_socket():
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
                stop([str(scripts / "fm-control.sh"), path.stem, "exit"])
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
    if shutil.which("no-mistakes"):
        stop(["no-mistakes", "daemon", "stop"])
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
    requested = False
    if remaining or errors:
        if primary:
            message = (
                "Stop work in this vessel for sailing. "
                "Stop the remaining vessel processes using their own stop commands; preserve "
                "all repositories, worktrees, dirty files and durable task records. Do not "
                "start new work or discard anything. The transfer will gracefully exit Pi "
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
                    fail("primary changed before shutdown; retry sailing")
                signal.pidfd_send_signal(fd, signal.SIGTERM)
                if not select.select([fd], [], [], 10)[0]:
                    errors.append(
                        "Pi did not finish graceful shutdown within 10 seconds"
                    )
            finally:
                os.close(fd)
        stop([str(scripts / "fm-watch-arm.sh"), "--stop"])
        if socket.is_socket() and not errors:
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
    }


def peer(anchor, operation):
    local = here()
    with anchor_lock(anchor):
        if operation == "inspect":
            record = (
                read_record(anchor) if (anchor / ".firstmate.json").exists() else None
            )
            available = False
            error = None
            if record is not None and record["harbor"] == local:
                try:
                    check_payload(anchor, record)
                    available = True
                except (OSError, ValueError) as failure:
                    error = str(failure)
            return {
                "here": local,
                "record": record,
                "available": available,
                "error": error,
            }
        request = json.load(sys.stdin)
        destination = harbor_name(request["destination"])
        record = read_record(anchor)
        if (
            destination == local
            or record != request["expected"]
            or record["harbor"] != local
        ):
            fail("source changed; rediscover before sailing")
        if operation == "prepare":
            if not shutil.which("rsync"):
                fail("rsync is required on the source harbor")
            retry = not record["anchored"] and (
                record.get("sailing_to") == destination
                or (
                    "sailing_to" not in record
                    and record.get("last_known_harbor") == destination
                )
            )
            if not record["anchored"] and not retry:
                fail("vessel is not available for this destination")
            check_payload(anchor, record)
            record = with_routing(record)
            record.update(
                anchored=False,
                sailing_to=destination,
                known_harbors=merge_harbors(
                    known_harbors(record), request["known_harbors"], [destination]
                ),
            )
            write_record(anchor, record)
            return {"here": local, "record": record, **stop_vessel(anchor, record)}
        elif operation == "commit":
            if record["anchored"] or record.get("sailing_to") != destination:
                fail("source is not sailing to this destination")
            record.pop("sailing_to")
            record["last_known_harbor"] = destination
        else:
            fail("unknown peer operation")
        write_record(anchor, record)
        return {"here": local, "record": record}


def locate(anchor, local, record, source):
    hints = merge_harbors(
        [source] if source else [],
        [record.get("last_known_harbor", record["harbor"])] if record else [],
        known_harbors(record) if record else [],
    )
    identity = record.get("vessel_id") if record else None
    seen = {local}
    found, retry = [], []
    owners = set()
    known = list(hints)
    index = 0
    while index < len(hints):
        host = hints[index]
        index += 1
        if host in seen:
            continue
        seen.add(host)
        try:
            reply = remote(anchor, host, "inspect")
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            subprocess.CalledProcessError,
        ) as error:
            reason = (
                (error.stderr or "SSH command failed").strip()
                if isinstance(error, subprocess.CalledProcessError)
                else str(error)
            )
            print(f"fm-anchor: cannot query {host}: {reason}", file=sys.stderr)
            continue
        candidate = reply["record"]
        if candidate is None or reply["here"] == local:
            continue
        if identity and candidate.get("vessel_id") != identity:
            continue
        if record and record["version"] == 1 and base_record(candidate) != record:
            continue
        if not identity:
            identity = candidate.get("vessel_id")
        known = merge_harbors(known, known_harbors(candidate), [reply["here"]])
        hints = merge_harbors(
            hints,
            [candidate.get("last_known_harbor", candidate["harbor"])],
            known_harbors(candidate),
            [candidate["sailing_to"]] if "sailing_to" in candidate else [],
        )
        if reply.get("error"):
            print(f"fm-anchor: cannot use {host}: {reply['error']}", file=sys.stderr)
        if (
            candidate["harbor"] != reply["here"]
            or reply.get("available") is not True
            or reply["here"] in owners
        ):
            continue
        owners.add(reply["here"])
        if candidate["anchored"]:
            found.append((host, candidate))
            if index == 1:
                return host, candidate, known
        elif candidate.get("sailing_to") == local or (
            candidate.get("last_known_harbor") == local
            and "sailing_to" not in candidate
            and record is not None
            and record.get("sailing_to") == local
            and record.get("last_known_harbor", record["harbor"]) == reply["here"]
        ):
            retry.append((host, candidate))
    choices = found or retry
    if len(choices) != 1:
        fail("no unique available vessel found; no new vessel was created")
    host, candidate = choices[0]
    return host, candidate, known


def sail(anchor, source, expected):
    local = here()
    if source:
        source = harbor_name(source)
    with anchor_lock(anchor):
        record = read_record(anchor) if (anchor / ".firstmate.json").exists() else None
        vessel = anchor / ".firstmate"
        if (
            record is None
            and vessel.exists()
            and (not vessel.is_dir() or any(vessel.iterdir()))
        ):
            fail("refusing to overwrite nonempty unregistered vessel state")
        if record and record["anchored"] and record["harbor"] == local:
            if base_record(record) != expected:
                fail(
                    "vessel is not anchored here with these options; metadata was not changed"
                )
            check_payload(anchor, record)
            upgraded = with_routing(record)
            if upgraded != json.loads((anchor / ".firstmate.json").read_text()):
                write_record(anchor, upgraded)
            return upgraded
        if record is None and source is None:
            fail("empty harbor; supply --from HARBOR or initialize explicitly")
        if not shutil.which("ssh") or not shutil.which("rsync"):
            fail("SSH and rsync are required for sailing")
        host, origin, known = locate(anchor, local, record, source)
        known = merge_harbors(known, known_harbors(origin), [origin["harbor"], local])
        if origin["options"] != expected["options"]:
            fail("destination directory options differ; source was not changed")
        print(
            f"fm-anchor: stopping source work at {host} before sailing", file=sys.stderr
        )
        deadline = time.monotonic() + 60
        while True:
            reply = remote(
                anchor,
                host,
                "prepare",
                {"expected": origin, "destination": local, "known_harbors": known},
            )
            prepared = reply["record"]
            if reply.get("stopped") is True:
                break
            reason = json.dumps(
                {"remaining": reply.get("remaining"), "errors": reply.get("errors")}
            )
            if not reply.get("requested") or time.monotonic() >= deadline:
                fail(
                    f"source work has not stopped; no data copied; retry sailing after resolving: {reason}"
                )
            print(
                f"fm-anchor: waiting for the source Pi agent to stop work: {reason}",
                file=sys.stderr,
            )
            origin = prepared
            time.sleep(2)
        if (
            prepared["anchored"]
            or prepared.get("sailing_to") != local
            or any(
                prepared[key] != origin[key]
                for key in ("anchor", "harbor", "backend", "options")
            )
            or (
                origin.get("vessel_id") and prepared["vessel_id"] != origin["vessel_id"]
            )
        ):
            fail("origin did not prepare this destination")
        incoming = {
            **prepared,
            "harbor": local,
            "last_known_harbor": prepared["harbor"],
        }
        write_record(anchor, incoming)
        exclusions = [
            str(Path(prepared["options"]["TMUX_TMPDIR"]).relative_to(vessel)) + "/"
        ]
        state = Path(prepared["options"]["FM_STATE_OVERRIDE"]).relative_to(vessel)
        exclusions.extend(
            str(state / name)
            for name in (".lock", ".lock-session", ".lock-session.prev")
        )
        filters = [
            "--exclude=/" + re.sub(r"([\\*?\[])", r"\\\1", path) for path in exclusions
        ]
        try:
            vessel.mkdir(mode=0o700, exist_ok=True)
            subprocess.run(
                [
                    "rsync",
                    "--archive",
                    "--delete",
                    "--delete-excluded",
                    "--partial",
                    "--no-owner",
                    "--no-group",
                    "--no-devices",
                    "--no-specials",
                    "--protect-args",
                    "-e",
                    shlex.join(["ssh", *SSH_OPTIONS]),
                    *filters,
                    "--",
                    f"{host}:{vessel}/",
                    str(vessel) + "/",
                ],
                check=True,
            )
            check_payload(anchor, prepared)
            committed = remote(
                anchor, host, "commit", {"expected": prepared, "destination": local}
            )["record"]
            expected_commit = {**prepared, "last_known_harbor": local}
            expected_commit.pop("sailing_to")
            if committed != expected_commit:
                fail("origin did not acknowledge arrival")
            for name in ("TMUX_TMPDIR", "XDG_CACHE_HOME"):
                Path(incoming["options"][name]).mkdir(
                    parents=True, exist_ok=True, mode=0o700
                )
            incoming.pop("sailing_to")
            incoming.update(anchored=True, last_known_harbor=local)
            write_record(anchor, incoming)
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            subprocess.CalledProcessError,
        ):
            print(
                f"fm-anchor: arrival interrupted; retry here using the inactive cache at {vessel}",
                file=sys.stderr,
            )
            raise
        return incoming


def main():
    if sys.platform != "linux":
        fail("vessels currently require Linux")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=("init", "check", "sail", "stop", "status", "_peer", "_projects"),
    )
    parser.add_argument("--anchor", required=True)
    parser.add_argument("--from", dest="source")
    parser.add_argument("--op", choices=("inspect", "prepare", "commit"))
    args = parser.parse_args()
    anchor = anchor_path(args.anchor)
    if args.operation in ("stop", "status"):
        expected = validate_record(json.load(sys.stdin), anchor)
        record = read_record(anchor)
        if record["harbor"] != here() or record["options"] != expected["options"]:
            fail("this vessel is not local with these directory options")
        if args.operation == "status":
            print(
                json.dumps(
                    {"record": record, "processes": vessel_processes(anchor, record)}
                )
            )
        else:
            with anchor_lock(anchor):
                if record != read_record(anchor):
                    fail("vessel changed before stopping; retry")
                result = stop_vessel(anchor, record)
                print(json.dumps(result))
                if not result["stopped"]:
                    sys.exit(1)
        return
    if args.operation == "sail":
        record = sail(
            anchor, args.source, validate_record(json.load(sys.stdin), anchor)
        )
    elif args.operation == "_peer":
        print(json.dumps(peer(anchor, args.op)))
        return
    else:
        expected = validate_record(json.load(sys.stdin), anchor)
        if args.operation == "init":
            record = with_routing(expected)
            write_record(anchor, record)
        else:
            record = read_record(anchor)
            if base_record(record) != expected:
                fail(
                    "vessel is not anchored here with these options; metadata was not changed"
                )
            if args.operation == "_projects":
                provision_projects(anchor, record)
    print(json.dumps(record))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("fm-anchor: interrupted; retry the requested operation", file=sys.stderr)
        sys.exit(130)
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"fm-anchor: {error}", file=sys.stderr)
        sys.exit(1)
