#!/usr/bin/env python3
"""Private metadata and sailing implementation for fm-anchor.sh.

Sailing stops source work before copying at identical physical anchor paths.
Host tools/authentication are not copied. Ordinary SSH configuration is used.

Records contain a stable vessel_id, last_known_harbor and known_harbors (SSH
names). harbor identifies the host owning this particular local record.
Host components are lowercase and known_harbors is deduplicated; SSH user name
case is preserved. check/run normalize in memory without rewriting; explicit
sail persists canonical names.

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
Pull preparation releases its lock between destination retries, allowing the
primary to finish cleanup. Sailing waits up to 60 seconds for coordination before
refusing without a copy. The source remains reserved for the same retry.

status lists accessible same-user /proc cwd/environment matches against vessel
paths, plus the recorded primary. This is not certification of every possible
writer: unrelated protected processes are not inspected, and a daemon that
clears its environment and leaves the vessel may not be attributable here.
Known but unverified primary ownership or remaining processes prevent transfer.
Only Pi is signalled directly; there is no name-pattern kill, force stop,
teardown, worktree return, or cleanup of unlanded work.

--to initiates an outbound-only push from a separate source terminal, holding
its anchor lock through handoff. receive reserves an inactive destination
before source stopping; shared preparation and rsync then copy the payload.
The receiver verifies it, the source commits its last-known hint, and activate
enables the receiver. No reverse SSH or new record fields are needed.
A lost activation acknowledgement is retried by recognizing the already active
destination, never by overwriting it with the source's inactive cache.

scuttle requires explicit --confirm and a separate terminal, stops local work,
and removes only .firstmate/NAME plus its sibling NAME.json and NAME.lock.
Only directories gain owner permissions; hardlinked files are unlinked without
changing outside Git objects. Directory symlinks are never followed. The lock
is removed last, and lock users reject stale descriptors after inode replacement.
No remote harbor, outside repository or unrelated anchor content is removed.
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
# Each CLI process selects one vessel; SSH peers receive the selection explicitly.
VESSEL_NAME = "flagship"


def vessel_path(anchor, suffix="", name=None):
    return anchor / ".firstmate" / ((name or VESSEL_NAME) + suffix)


def valid_name(name):
    return re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name) is not None


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
    for path in (
        anchor / ".firstmate",
        vessel_path(anchor),
        vessel_path(anchor, ".json"),
    ):
        if path.is_symlink():
            fail("vessel and metadata must not be symlinks")
    return anchor


def validate_configuration(record, anchor, vessel_name=None):
    if (
        not isinstance(record, dict)
        or set(record) != BASE_FIELDS
        or type(record.get("version")) is not int
        or record["version"] != 2
    ):
        fail("invalid vessel configuration")
    if record["anchor"] != str(anchor) or type(record["anchored"]) is not bool:
        fail("anchor path differs; sailing requires identical physical paths")
    record["harbor"] = harbor_name(record["harbor"])
    if (
        record["backend"] != "tmux"
        or not isinstance(record["options"], dict)
        or set(record["options"]) != OPTION_NAMES
    ):
        fail("invalid vessel configuration")
    vessel = vessel_path(anchor, name=vessel_name)
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
    return record


def validate_record(record, anchor, vessel_name=None):
    if not isinstance(record, dict):
        fail("invalid vessel record")
    fields = BASE_FIELDS | (ROUTE_FIELDS - {"sailing_to"})
    if "sailing_to" in record:
        fields |= {"sailing_to"}
    if set(record) != fields:
        fail("invalid vessel record")
    record.update(
        validate_configuration(record_configuration(record), anchor, vessel_name)
    )
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


def read_record(anchor, name=None):
    path = vessel_path(anchor, ".json", name)
    if path.is_symlink() or not path.is_file():
        fail("vessel record must be a regular file")
    with path.open() as stream:
        return validate_record(json.load(stream), anchor, name)


def record_configuration(record):
    return {key: value for key, value in record.items() if key not in ROUTE_FIELDS}


def write_record(anchor, record):
    validate_record(record, anchor)
    if vessel_path(anchor, ".json").is_symlink():
        fail("vessel record must not be a symlink")
    fd, name = tempfile.mkstemp(
        prefix=f".{VESSEL_NAME}.json.", dir=vessel_path(anchor).parent
    )
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(record, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, vessel_path(anchor, ".json"))
        directory = os.open(vessel_path(anchor).parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def anchor_lock(anchor):
    vessel_path(anchor).parent.mkdir(mode=0o700, exist_ok=True)
    path = vessel_path(anchor, ".lock")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            fail("anchor lock must be a regular file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail("another anchor operation is in progress")
        opened = os.fstat(fd)
        current = path.stat(follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            fail("anchor lock changed; retry the operation")
        yield
    finally:
        os.close(fd)


def known_harbors(record):
    return record["known_harbors"]


def merge_harbors(*groups):
    return list(dict.fromkeys(harbor_name(name) for group in groups for name in group))


def check_payload(anchor, record, payload=None):
    vessel = vessel_path(anchor)
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
        if (source / ".git").is_file():
            git_dirs = subprocess.run(
                [
                    "git",
                    "-C",
                    str(source),
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-dir",
                    "--git-common-dir",
                ],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.splitlines()
            if Path(git_dirs[0]).resolve() != Path(git_dirs[1]).resolve():
                print(f"fm-anchor: skipping linked worktree {source}", file=sys.stderr)
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
        # get-url can return the name 'origin' when its configured URL is empty.
        configured_origin = subprocess.run(
            ["git", "-C", str(source), "config", "--get", "remote.origin.url"],
            check=False,
            text=True,
            capture_output=True,
        )
        if configured_origin.returncode == 1 or (
            configured_origin.returncode == 0 and not configured_origin.stdout.strip()
        ):
            print(f"fm-anchor: skipping {relative}: no origin URL", file=sys.stderr)
            continue
        if configured_origin.returncode:
            fail(f"{source}: cannot read origin: {configured_origin.stderr.strip()}")
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
                "--single-branch",
                "--branch",
                "main",
                "--origin",
                "local",
                "--dissociate",
                "--",
                str(source),
                str(destination),
            ],
            cwd=source,
            stdout=sys.stderr,
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(destination),
                "remote",
                "add",
                "--track",
                "main",
                "origin",
                origin.stdout.strip(),
            ],
            stdout=sys.stderr,
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(destination),
                "config",
                "--local",
                "branch.main.remote",
                "origin",
            ],
            stdout=sys.stderr,
            check=True,
        )


def remote(anchor, host, operation, request=None):
    host = harbor_name(host)
    script = 'exec "${XDG_DATA_HOME:-$HOME/.local/share}/firstmate/bin/fm-anchor.sh" _peer --anchor "$1" --vessel "$3" --op "$2"'
    command = shlex.join(
        ["sh", "-c", script, "sh", str(anchor), operation, VESSEL_NAME]
    )
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
    vessel = vessel_path(anchor)
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


def prepare_source(anchor, record, destination, known):
    if not shutil.which("rsync"):
        fail("rsync is required on the source harbor")
    retry = not record["anchored"] and (
        record.get("sailing_to") == destination
        or ("sailing_to" not in record and record["last_known_harbor"] == destination)
    )
    if not record["anchored"] and not retry:
        fail("vessel is not available for this destination")
    check_payload(anchor, record)
    record = dict(record)
    record.update(
        anchored=False,
        sailing_to=destination,
        known_harbors=merge_harbors(known_harbors(record), known, [destination]),
    )
    write_record(anchor, record)
    return {"record": record, **stop_vessel(anchor, record)}


def commit_source(anchor, record, destination):
    if record["anchored"] or record.get("sailing_to") != destination:
        fail("source is not sailing to this destination")
    record = {**record, "last_known_harbor": destination}
    record.pop("sailing_to")
    write_record(anchor, record)
    return record


def check_receiver(record, source, local):
    if source["harbor"] == local:
        fail("receiving requires another harbor's identified vessel")
    if record is not None:
        if (
            record["harbor"] != local
            or record["anchored"]
            or record["vessel_id"] != source["vessel_id"]
            or record["options"] != source["options"]
        ):
            fail("destination is anchored, unrelated or configured differently")
        if "sailing_to" in record and not (
            record["sailing_to"] == local
            and record["last_known_harbor"] == source["harbor"]
        ):
            fail("destination is reserved for another handoff")
    if (
        not source["anchored"]
        and source.get("sailing_to") != local
        and not (
            "sailing_to" not in source
            and source["last_known_harbor"] == local
            and record is not None
            and record.get("sailing_to") == local
            and record["last_known_harbor"] == source["harbor"]
        )
    ):
        fail("inactive cache is not an authoritative source for this destination")


def activate_payload(anchor, record):
    check_payload(anchor, record)
    for name in ("TMUX_TMPDIR", "XDG_CACHE_HOME"):
        Path(record["options"][name]).mkdir(parents=True, exist_ok=True, mode=0o700)
    record = {**record, "anchored": True, "last_known_harbor": record["harbor"]}
    record.pop("sailing_to")
    write_record(anchor, record)
    return record


def rsync_payload(anchor, record, source, destination):
    vessel = vessel_path(anchor)
    exclusions = [str(Path(record["options"]["TMUX_TMPDIR"]).relative_to(vessel)) + "/"]
    state = Path(record["options"]["FM_STATE_OVERRIDE"]).relative_to(vessel)
    exclusions.extend(
        str(state / name) for name in (".lock", ".lock-session", ".lock-session.prev")
    )
    filters = [
        "--exclude=/" + re.sub(r"([\\*?\[])", r"\\\1", path) for path in exclusions
    ]
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
            source,
            destination,
        ],
        check=True,
    )


def peer(anchor, operation):
    local = here()
    with anchor_lock(anchor):
        if operation == "inspect":
            record = (
                read_record(anchor) if vessel_path(anchor, ".json").exists() else None
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
        if operation == "receive":
            source = validate_record(request["source"], anchor)
            record = (
                read_record(anchor) if vessel_path(anchor, ".json").exists() else None
            )
            if record != request["expected"]:
                fail("destination changed before receiving; retry sailing")
            check_receiver(record, source, local)
            vessel = vessel_path(anchor)
            if (
                record is None
                and vessel.exists()
                and (not vessel.is_dir() or any(vessel.iterdir()))
            ):
                fail("refusing to overwrite nonempty unregistered vessel state")
            if not shutil.which("rsync"):
                fail("rsync is required on the destination harbor")
            incoming = {
                **source,
                "anchored": False,
                "harbor": local,
                "last_known_harbor": source["harbor"],
                "sailing_to": local,
                "known_harbors": merge_harbors(
                    known_harbors(source),
                    known_harbors(record) if record else [],
                    [local],
                ),
            }
            vessel.mkdir(mode=0o700, exist_ok=True)
            write_record(anchor, incoming)
            return {"here": local, "record": incoming}
        if operation == "activate":
            source = validate_record(request["source"], anchor)
            expected = validate_record(request["expected"], anchor)
            if (
                source["anchored"]
                or "sailing_to" in source
                or source["last_known_harbor"] != local
                or source["harbor"] != expected["last_known_harbor"]
                or source["vessel_id"] != expected["vessel_id"]
                or source["options"] != expected["options"]
                or expected.get("sailing_to") != local
            ):
                fail("source did not acknowledge this arrival")
            record = read_record(anchor)
            completed = {**expected, "anchored": True, "last_known_harbor": local}
            completed.pop("sailing_to")
            if record == completed:
                check_payload(anchor, record)
            elif record == expected and record["harbor"] == local:
                record = activate_payload(anchor, record)
            else:
                fail("destination changed before activation; retry sailing")
            return {"here": local, "record": record}
        destination = harbor_name(request["destination"])
        record = read_record(anchor)
        if (
            destination == local
            or record != request["expected"]
            or record["harbor"] != local
        ):
            fail("source changed; rediscover before sailing")
        if operation == "prepare":
            return {
                "here": local,
                **prepare_source(anchor, record, destination, request["known_harbors"]),
            }
        if operation == "commit":
            return {"here": local, "record": commit_source(anchor, record, destination)}
        fail("unknown peer operation")


def locate(anchor, local, record, source):
    hints = merge_harbors(
        [source] if source else [],
        [record["last_known_harbor"]] if record else [],
        known_harbors(record) if record else [],
    )
    identity = record["vessel_id"] if record else None
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
        if identity and candidate["vessel_id"] != identity:
            continue
        if not identity:
            identity = candidate["vessel_id"]
        known = merge_harbors(known, known_harbors(candidate), [reply["here"]])
        hints = merge_harbors(
            hints,
            [candidate["last_known_harbor"]],
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
            candidate["last_known_harbor"] == local
            and "sailing_to" not in candidate
            and record is not None
            and record.get("sailing_to") == local
            and record["last_known_harbor"] == reply["here"]
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
        record = read_record(anchor) if vessel_path(anchor, ".json").exists() else None
        vessel = vessel_path(anchor)
        if (
            record is None
            and vessel.exists()
            and (not vessel.is_dir() or any(vessel.iterdir()))
        ):
            fail("refusing to overwrite nonempty unregistered vessel state")
        if record and record["anchored"] and record["harbor"] == local:
            if record_configuration(record) != expected:
                fail(
                    "vessel is not anchored here with these options; metadata was not changed"
                )
            check_payload(anchor, record)
            if record != json.loads(vessel_path(anchor, ".json").read_text()):
                write_record(anchor, record)
            return record
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
            or prepared["vessel_id"] != origin["vessel_id"]
        ):
            fail("origin did not prepare this destination")
        incoming = {
            **prepared,
            "harbor": local,
            "last_known_harbor": prepared["harbor"],
        }
        write_record(anchor, incoming)
        try:
            rsync_payload(anchor, prepared, f"{host}:{vessel}/", str(vessel) + "/")
            check_payload(anchor, prepared)
            committed = remote(
                anchor, host, "commit", {"expected": prepared, "destination": local}
            )["record"]
            expected_commit = {**prepared, "last_known_harbor": local}
            expected_commit.pop("sailing_to")
            if committed != expected_commit:
                fail("origin did not acknowledge arrival")
            incoming = activate_payload(anchor, incoming)
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


def require_separate_terminal(record):
    pid = session_pid(record)
    if pid.isdigit() and int(pid) in ancestors(os.getpid()):
        fail(
            "run this operation from a separate terminal; stopping this Pi would interrupt it"
        )


def scuttle(anchor, expected):
    with anchor_lock(anchor):
        record = (
            read_record(anchor) if vessel_path(anchor, ".json").exists() else expected
        )
        require_separate_terminal(record)
        deadline = time.monotonic() + 60
        while True:
            stopped = stop_vessel(anchor, record)
            if stopped["stopped"]:
                break
            if not stopped["requested"] or time.monotonic() >= deadline:
                fail(
                    f"work has not stopped; vessel was not deleted: {json.dumps(stopped)}"
                )
            print(
                f"fm-anchor: waiting for requested cleanup before scuttling: {json.dumps(stopped)}",
                file=sys.stderr,
            )
            time.sleep(2)

        def writable_directories(fd):
            # Do not chmod files: Git objects may be hardlinked to outside repos.
            os.fchmod(fd, stat.S_IMODE(os.fstat(fd).st_mode) | stat.S_IRWXU)
            with os.scandir(fd) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        child = os.open(
                            entry.name,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=fd,
                        )
                        try:
                            writable_directories(child)
                        finally:
                            os.close(child)

        vessel = vessel_path(anchor)
        if vessel.exists():
            fd = os.open(vessel, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                writable_directories(fd)
            finally:
                os.close(fd)
            shutil.rmtree(vessel)
        vessel_path(anchor, ".json").unlink(missing_ok=True)
        # Last mutation under this lock; stale openers must reject its old inode.
        vessel_path(anchor, ".lock").unlink()
    return {"scuttled": True}


def push(anchor, target, expected):
    local = here()
    host = harbor_name(target)
    if host == local:
        fail("destination is this harbor")
    with anchor_lock(anchor):
        record = read_record(anchor)
        configuration = {**record_configuration(record), "anchored": True}
        if configuration != expected:
            fail("this vessel does not belong to this harbor with these options")
        require_separate_terminal(record)
        if not shutil.which("ssh") or not shutil.which("rsync"):
            fail("SSH and rsync are required for sailing")
        reply = remote(anchor, host, "inspect")
        destination = reply["here"]
        if destination == local:
            fail("destination is this harbor, including through an SSH alias")
        candidate = reply["record"]
        if candidate is not None and candidate["anchored"]:
            if (
                not record["anchored"]
                and "sailing_to" not in record
                and record["last_known_harbor"] == destination
                and candidate["harbor"] == destination
                and candidate["vessel_id"] == record["vessel_id"]
                and candidate["options"] == record["options"]
                and reply.get("available") is True
            ):
                return candidate
            fail("destination is already anchored; no source work was stopped")
        check_receiver(candidate, record, destination)
        known = merge_harbors(
            known_harbors(record),
            known_harbors(candidate) if candidate else [],
            [host, destination],
        )
        record["known_harbors"] = known
        write_record(anchor, record)
        incoming = remote(
            anchor, host, "receive", {"expected": candidate, "source": record}
        )["record"]
        expected_incoming = {
            **record,
            "anchored": False,
            "harbor": destination,
            "last_known_harbor": local,
            "sailing_to": destination,
        }
        if incoming != expected_incoming:
            fail("destination did not reserve this arrival; no source work was stopped")
        deadline = time.monotonic() + 60
        while True:
            stopped = prepare_source(anchor, record, destination, known)
            record = stopped["record"]
            if stopped["stopped"]:
                break
            reason = json.dumps(
                {"remaining": stopped["remaining"], "errors": stopped["errors"]}
            )
            if not stopped["requested"] or time.monotonic() >= deadline:
                fail(
                    f"source work has not stopped; no data copied; retry pushing after resolving: {reason}"
                )
            print(
                f"fm-anchor: waiting for local source cleanup: {reason}",
                file=sys.stderr,
            )
            time.sleep(2)
        print(f"fm-anchor: pushing the stopped vessel to {host}", file=sys.stderr)
        vessel = vessel_path(anchor)
        rsync_payload(anchor, record, str(vessel) + "/", f"{host}:{vessel}/")
        copied = remote(anchor, host, "inspect")
        if (
            copied["here"] != destination
            or copied["record"] != incoming
            or copied.get("available") is not True
        ):
            fail(
                f"destination did not verify the copied payload: {copied.get('error')}"
            )
        committed = commit_source(anchor, record, destination)
        arrived = remote(
            anchor, host, "activate", {"expected": incoming, "source": committed}
        )["record"]
        completed = {**incoming, "anchored": True, "last_known_harbor": destination}
        completed.pop("sailing_to")
        if arrived != completed:
            fail(
                "destination did not acknowledge activation; retry pushing to this harbor"
            )
        return arrived


def main():
    if sys.platform != "linux":
        fail("vessels currently require Linux")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=(
            "init",
            "check",
            "sail",
            "stop",
            "status",
            "scuttle",
            "_peer",
            "_projects",
            "list",
        ),
    )
    parser.add_argument("--anchor", required=True)
    parser.add_argument("--vessel", default="flagship")
    parser.add_argument("--confirm", action="store_true")
    direction = parser.add_mutually_exclusive_group()
    direction.add_argument("--from", dest="source")
    direction.add_argument("--to", dest="target")
    parser.add_argument(
        "--op", choices=("inspect", "prepare", "commit", "receive", "activate")
    )
    args = parser.parse_args()
    global VESSEL_NAME
    if not valid_name(args.vessel):
        parser.error(
            "vessel name must be 1-64 lowercase letters, digits, underscores or hyphens, starting with a letter or digit"
        )
    VESSEL_NAME = args.vessel
    if args.operation == "scuttle" and not args.confirm:
        parser.error("scuttle requires --confirm to discard all local vessel work")
    anchor = anchor_path(args.anchor)
    if args.operation == "list":
        names = []
        namespace = anchor / ".firstmate"
        if namespace.exists():
            for path in sorted(namespace.glob("*.json")):
                if valid_name(path.stem):
                    read_record(anchor, path.stem)
                    names.append(path.stem)
        if names:
            print("\n".join(names))
        return
    if args.operation == "scuttle":
        print(
            json.dumps(
                scuttle(anchor, validate_configuration(json.load(sys.stdin), anchor))
            )
        )
        return
    if args.operation in ("stop", "status"):
        expected = validate_configuration(json.load(sys.stdin), anchor)
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
        expected = validate_configuration(json.load(sys.stdin), anchor)
        record = (
            push(anchor, args.target, expected)
            if args.target
            else sail(anchor, args.source, expected)
        )
    elif args.operation == "_peer":
        print(json.dumps(peer(anchor, args.op)))
        return
    else:
        expected = validate_configuration(json.load(sys.stdin), anchor)
        if args.operation == "init":
            record = {
                **expected,
                "vessel_id": str(uuid.uuid4()),
                "last_known_harbor": expected["harbor"],
                "known_harbors": [expected["harbor"]],
            }
            write_record(anchor, record)
        else:
            record = read_record(anchor)
            if record_configuration(record) != expected:
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
