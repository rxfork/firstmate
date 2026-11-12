#!/usr/bin/env python3
"""Private metadata and sailing implementation for fm-anchor.sh.

Sailing requires manually stopped work and identical physical anchor paths.
Host tools/authentication are not copied. Ordinary SSH configuration is used.

Record owner: version 1 contains the original local anchor configuration;
version 2 adds a stable vessel_id, last_known_harbor and known_harbors (SSH
names). harbor still identifies the host owning this particular local record.
check/run accept version 1 without rewriting; explicit sail upgrades it.

Peer inspect reads a record and tests payload availability. prepare compares
that exact record under the anchor lock, marks it inactive and reserves it for
sailing_to. The receiving record reserves itself with the same field, keeping
its previous payload unavailable as a source. Only that destination may retry.
After rsync, commit compares the
prepared record, clears sailing_to and updates the origin's last-known hint.
The destination activates only after that acknowledgement and payload install.
Interrupted transfers remain inactive and retry at the destination. No receipts,
leases, global registry or automatic process stopping are involved.
"""

import argparse
import fcntl
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
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
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.@-]*", value
    ):
        fail("harbor must be an SSH host name or configured alias")
    return value


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
    harbor_name(record["harbor"])
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
        harbor_name(record["last_known_harbor"])
        known = record["known_harbors"]
        if (
            not isinstance(known, list)
            or not known
            or any(not isinstance(name, str) for name in known)
        ):
            fail("invalid known harbors")
        for name in known:
            harbor_name(name)
        if len(set(known)) != len(known) or not {
            record["harbor"],
            record["last_known_harbor"],
        }.issubset(known):
            fail("invalid known harbors")
        if "sailing_to" in record:
            harbor_name(record["sailing_to"])
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
    return list(dict.fromkeys(name for group in groups for name in group))


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


def remote(anchor, host, operation, request=None):
    harbor_name(host)
    script = 'exec "${XDG_DATA_HOME:-$HOME/.local/share}/firstmate/bin/fm-anchor.sh" _peer --anchor "$1" --op "$2"'
    command = shlex.join(["sh", "-c", script, "sh", str(anchor), operation])
    result = subprocess.run(
        ["ssh", *SSH_OPTIONS, "--", host, command],
        input=json.dumps(request) if request is not None else "",
        text=True,
        capture_output=True,
        check=True,
    )
    reply = json.loads(result.stdout)
    harbor_name(reply["here"])
    if reply["record"] is not None:
        validate_record(reply["record"], anchor)
    return reply


def peer(anchor, operation):
    local = here()
    with anchor_lock(anchor):
        if operation == "inspect":
            record = (
                read_record(anchor) if (anchor / ".firstmate.json").exists() else None
            )
            available = False
            if record is not None and record["harbor"] == local:
                try:
                    check_payload(anchor, record)
                    available = True
                except (OSError, ValueError):
                    pass
            return {"here": local, "record": record, "available": available}
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
            and (
                record is None
                or record.get("last_known_harbor", record["harbor"]) == reply["here"]
            )
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
        harbor_name(source)
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
            if upgraded != record:
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
            f"fm-anchor: sailing from {host}; all source work must already be manually stopped",
            file=sys.stderr,
        )
        prepared = remote(
            anchor,
            host,
            "prepare",
            {"expected": origin, "destination": local, "known_harbors": known},
        )["record"]
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
        stage = Path(tempfile.mkdtemp(prefix=".firstmate.sailing.", dir=anchor))
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
            subprocess.run(
                [
                    "rsync",
                    "--archive",
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
                    str(stage) + "/",
                ],
                check=True,
            )
            check_payload(anchor, prepared, stage)
            committed = remote(
                anchor, host, "commit", {"expected": prepared, "destination": local}
            )["record"]
            expected_commit = {**prepared, "last_known_harbor": local}
            expected_commit.pop("sailing_to")
            if committed != expected_commit:
                fail("origin did not acknowledge arrival")
            if vessel.exists():
                previous = anchor / (".firstmate.previous." + uuid.uuid4().hex)
                vessel.rename(previous)
                print(
                    f"fm-anchor: previous local copy preserved at {previous}",
                    file=sys.stderr,
                )
            stage.rename(vessel)
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
            saved = stage if stage.exists() else vessel
            print(
                f"fm-anchor: arrival interrupted; retry here; copied data preserved at {saved}",
                file=sys.stderr,
            )
            raise
        return incoming


def main():
    if sys.platform != "linux":
        fail("vessels currently require Linux")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("init", "check", "sail", "_peer"))
    parser.add_argument("--anchor", required=True)
    parser.add_argument("--from", dest="source")
    parser.add_argument("--op", choices=("inspect", "prepare", "commit"))
    args = parser.parse_args()
    anchor = anchor_path(args.anchor)
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
    print(json.dumps(record))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(
            "fm-anchor: interrupted; retry sailing at the destination", file=sys.stderr
        )
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
