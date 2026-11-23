"""Host-local fullstop receipts and tmux endpoint recovery for anchored vessels."""

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from fm_raise_anchor import process_info, tmux_server_absent, vessel_environment

SCRIPTS = Path(__file__).resolve().parent
NOTE = (
    "The vessel was fully stopped or its shutdown was interrupted. Continue the original "
    "task from this existing worktree and its durable status and inbox. Review dirty files, "
    "branch and PR state before acting. Preserve existing validation ownership and pending "
    "decisions; do not re-run validation or merge based on this recovery."
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def identity(pid):
    try:
        p = process_info(pid)
    except (FileNotFoundError, ProcessLookupError):
        return None
    return None if p["state"] in ("Z", "X") else [p["pid"], p["start"]]


class Recovery:
    def __init__(self, anchor, record, lock_fd, arrival=False):
        self.options = record["options"]
        self.env = vessel_environment(anchor, record)
        self.env["FM_VESSEL_RECOVERY_FD"] = str(lock_fd)
        self.lock_fd = lock_fd
        self.state = Path(self.options["FM_STATE_OVERRIDE"])
        self.path = self.state / ".vessel-recovery.json"
        self.socket = (
            Path(self.options["TMUX_TMPDIR"]) / f"tmux-{os.getuid()}" / "default"
        )
        self.boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        self.owner = [record["vessel_id"], record["harbor"], digest(self.options)]
        self.receipt = None
        if self.path.is_symlink() or self.path.exists():
            st = self.path.lstat()
            if (
                not stat.S_ISREG(st.st_mode)
                or st.st_uid != os.getuid()
                or st.st_mode & 0o077
            ):
                raise ValueError(f"{self.path}: receipt must be a private regular file")
            try:
                envelope = json.loads(self.path.read_text())
                body = envelope["body"]
                if envelope["sha256"] != digest(body) or body["version"] != 1:
                    raise ValueError("invalid version or checksum")
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError(
                    f"{self.path}: corrupt fullstop receipt: {exc}"
                ) from exc
            transferred = (
                arrival
                and record.get("handoff_id")
                and body.get("handoff_id") == record["handoff_id"]
                and body["owner"]
                == [self.owner[0], record["last_known_harbor"], self.owner[2]]
            )
            if arrival and not transferred and body["phase"] == "complete":
                self.path.unlink()
                return
            if not transferred and (
                body["owner"] != self.owner or body["socket"] != str(self.socket)
            ):
                raise ValueError(
                    f"{self.path}: fullstop receipt belongs to a different vessel, harbor or socket"
                )
            self.receipt = body
            if transferred:
                self.adopt_arrival()
            elif arrival and body.get("handoff_id") != record.get("handoff_id"):
                raise ValueError("recovery receipt belongs to another handoff")

    def save(self):
        fd, name = tempfile.mkstemp(prefix=".vessel-recovery.", dir=self.state)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(
                    {"body": self.receipt, "sha256": digest(self.receipt)}, stream
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            directory = os.open(self.state, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(name).unlink(missing_ok=True)

    def run(self, *args, check=True):
        return subprocess.run(
            args,
            env=self.env,
            pass_fds=(self.lock_fd,) if Path(args[0]).name == "fm-control.sh" else (),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            text=True,
            check=check,
        )

    def tmux(self, *args, stderr=None):
        # Do not pass the coordination lock into the server's environment.
        return subprocess.check_output(
            ["tmux", "-S", str(self.socket), *args],
            env={**self.env, "FM_VESSEL_RECOVERY_FD": ""},
            text=True,
            stderr=stderr,
        ).strip()

    def server(self):
        try:
            before = self.socket.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISSOCK(before.st_mode) or before.st_uid != os.getuid():
            raise ValueError(f"not an owned tmux socket: {self.socket}")
        try:
            pid = self.tmux("display-message", "-p", "#{pid}", stderr=subprocess.PIPE)
            proc = identity(int(pid))
            after = self.socket.lstat()
        except subprocess.CalledProcessError as exc:
            # The receipt must still prove that workers stopped.
            if tmux_server_absent(exc.returncode, exc.stderr, self.socket):
                return None
            sys.stderr.write(exc.stderr)
            raise ValueError(f"tmux socket unavailable: {self.socket}") from exc
        except (OSError, ValueError) as exc:
            raise ValueError(f"tmux socket unavailable: {self.socket}") from exc
        if not proc or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError(f"tmux server changed during inspection: {self.socket}")
        return [self.boot, proc, after.st_dev, after.st_ino]

    def panes(self, server):
        if server is None:
            return {}
        rows = self.tmux(
            "list-panes",
            "-a",
            "-F",
            "#{session_name}:#{window_name}\t#{window_id}\t#{pane_id}\t#{pane_pid}\t#{pane_current_path}",
        )
        result = {}
        for row in rows.splitlines():
            endpoint, window, pane, pid, cwd = row.split("\t", 4)
            result.setdefault(endpoint, []).append(
                [window, pane, identity(int(pid)), cwd]
            )
        if self.server() != server:
            raise ValueError("tmux server changed during window inventory")
        return result

    def metadata(self, task):
        path = self.state / f"{task}.meta"
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", task)
            or path.is_symlink()
            or not path.is_file()
        ):
            raise ValueError(f"invalid vessel task record: {path}")
        meta = {}
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep:
                if key in meta:
                    raise ValueError(f"{task}: duplicate task field {key}")
                meta[key] = value
        return meta

    def task_identity(self, task, meta):
        worktree = Path(meta.get("worktree", ""))
        project = Path(meta.get("project", ""))
        brief = Path(self.options["FM_DATA_OVERRIDE"]) / task / "brief.md"
        if (
            meta.get("backend", "tmux") != "tmux"
            or meta.get("endpoint_task_id", task) != task
            or meta.get("remote_host")
            or meta.get("home")
        ):
            raise ValueError(f"{task}: task record is not a local vessel worker")
        if meta.get("kind", "ship") not in ("ship", "scout"):
            raise ValueError(
                f"{task}: fullstop recovery does not own this task kind ({meta['kind']})"
            )
        if (
            not worktree.is_absolute()
            or not worktree.is_dir()
            or not worktree.resolve().is_relative_to(
                Path(self.options["TREEHOUSE_ROOT"])
            )
        ):
            raise ValueError(
                f"{task}: isolated vessel worktree is missing or outside this vessel: {worktree}"
            )
        if not project.is_dir() or not project.resolve().is_relative_to(
            Path(self.options["FM_PROJECTS_OVERRIDE"])
        ):
            raise ValueError(f"{task}: project is not owned by this vessel: {project}")
        if not brief.is_file() or not brief.resolve().is_relative_to(
            Path(self.options["FM_DATA_OVERRIDE"])
        ):
            raise ValueError(f"{task}: missing vessel instructions at {brief}")
        endpoint = meta.get("window", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+:fm-" + re.escape(task), endpoint):
            raise ValueError(f"{task}: invalid vessel endpoint {endpoint!r}")
        st = worktree.stat()
        return {
            "endpoint": endpoint,
            "worktree": str(worktree.resolve()),
            "directory": [st.st_dev, st.st_ino],
            "harness": meta.get("harness"),
            "project": str(project.resolve()),
            "brief": str(brief),
            "kind": meta.get("kind", "ship"),
            "branch": meta.get("branch"),
            "mode": meta.get("mode"),
        }

    def crew_waiting(self, task):
        if any(
            (self.state / f"{task}.{suffix}").is_file()
            for suffix in ("review-park", "backlog-close")
        ):
            return True
        verdict = self.run(SCRIPTS / "fm-crew-state.sh", task).stdout
        return verdict.startswith(
            (
                "state: done ·",
                "state: parked ·",
                "state: paused ·",
                "state: working · source: run-step",
            )
        )

    def daemon_running(self):
        if not shutil.which("no-mistakes"):
            return False
        result = self.run("no-mistakes", "daemon", "status")
        status = result.stdout.lower()
        if "not running" in status or "stopped" in status:
            return False
        if "running" in status:
            return True
        raise ValueError("cannot verify the vessel's no-mistakes daemon status")

    def adopt_arrival(self):
        receipt = self.receipt
        tasks = receipt["tasks"]
        if (
            receipt["phase"] != "stopping"
            or not receipt["shutdown"]
            or self.server() is not None
            or set(tasks) != {path.stem for path in self.state.glob("*.meta")}
        ):
            raise ValueError("arrival does not have a verified worker shutdown")
        for task, entry in tasks.items():
            meta = self.metadata(task)
            current = self.task_identity(task, meta)
            # Rsync changes the worktree inode, not the rest of its task identity.
            if (
                entry["outcome"] not in ("stopped", "parked")
                or entry["generation"] != meta.get("spawn_gen")
                or {**current, "directory": entry["identity"]["directory"]}
                != entry["identity"]
            ):
                raise ValueError(f"{task}: arrival does not match the stopped worker")
            entry["identity"] = current
        receipt.update(owner=self.owner, socket=str(self.socket), server=None)
        self.save()

    def begin_stop(self, handoff_id=None):
        previous = self.receipt
        if previous and previous["phase"] == "stopping":
            return
        if previous and previous["phase"] == "resuming":
            raise ValueError("resume is incomplete; retry resume before a new fullstop")
        server = self.server()
        panes = self.panes(server)
        tasks = {}
        for path in sorted(self.state.glob("*.meta")):
            task = path.stem
            meta = self.metadata(task)
            task_identity = self.task_identity(task, meta)
            endpoint = task_identity["endpoint"]
            workers = panes.get(endpoint, [])
            if not workers:
                prior = previous["tasks"].get(task) if previous else None
                if (
                    prior
                    and prior["identity"] == task_identity
                    and prior["outcome"] == "parked"
                    and prior["generation"] == meta.get("spawn_gen")
                ):
                    tasks[task] = prior
                    continue
                raise ValueError(
                    f"{task}: original endpoint {endpoint} not found on {self.socket}; no verified stop receipt. Reconnect its original server and retry fullstop; do not recreate a lookalike window"
                )
            if (
                len(workers) != 1
                or not workers[0][2]
                or not Path(workers[0][3])
                .resolve()
                .is_relative_to(Path(task_identity["worktree"]))
            ):
                raise ValueError(
                    f"{task}: endpoint {endpoint} is ambiguous or is not in its recorded worktree"
                )
            tasks[task] = {
                "identity": task_identity,
                "pane": workers[0][:3],
                "generation": meta.get("spawn_gen"),
                "validation_wait": self.crew_waiting(task),
                "outcome": "pending",
            }
        self.receipt = {
            "version": 1,
            "owner": self.owner,
            "socket": str(self.socket),
            "server": server,
            "tasks": tasks,
            "phase": "stopping",
            "shutdown": False,
            "token": uuid.uuid4().hex,
            "restart_validation": self.daemon_running(),
        }
        if handoff_id is not None:
            self.receipt["handoff_id"] = handoff_id
        self.save()

    def check_original_server(self, server):
        old = self.receipt["server"]
        if server == old:
            return
        if server is not None:
            raise ValueError(
                f"tmux socket path reused by a different server: {self.socket}"
            )
        if old and (
            not self.receipt["shutdown"]
            or (old[0] == self.boot and identity(old[1][0]) == old[1])
        ):
            raise ValueError(
                f"original tmux server unavailable without verified shutdown: {self.socket}"
            )

    def stop_workers(self):
        server = self.server()
        self.check_original_server(server)
        panes = self.panes(server)
        for task, entry in self.receipt["tasks"].items():
            if entry["outcome"] != "pending":
                continue
            meta = self.metadata(task)
            self.check_task(task, entry, meta)
            current = panes.get(entry["identity"]["endpoint"], [])
            if len(current) != 1 or current[0][:3] != entry["pane"]:
                raise ValueError(
                    f"{task}: original pane is unavailable or changed before its stop could be verified"
                )
            result = self.run(SCRIPTS / "fm-control.sh", task, "exit", check=False)
            if result.returncode or not result.stdout.startswith(
                (
                    f"stopped {task} ",
                    f"already-stopped {task} ",
                )
            ):
                raise ValueError(
                    f"{task}: fm-control exit did not verify a stopped worker"
                )
            entry["outcome"] = "parked" if entry["validation_wait"] else "stopped"
            self.save()

    def before_server_stop(self):
        self.check_original_server(self.server())
        self.receipt["shutdown"] = True
        self.save()

    def check_task(self, task, entry, meta):
        if self.task_identity(task, meta) != entry["identity"]:
            raise ValueError(
                f"{task}: task identity or isolated directory changed since fullstop"
            )
        if (
            meta.get("spawn_gen") != entry["generation"]
            and meta.get("control_relaunch_tx") != self.receipt["token"]
        ):
            raise ValueError(f"{task}: unexpected worker generation since fullstop")

    def agent_state(self, endpoint):
        return self.run(
            "bash",
            "-c",
            'set -eu; . "$1/fm-backend.sh"; fm_backend_agent_state tmux "$2"',
            "bash",
            SCRIPTS,
            endpoint,
        ).stdout.strip()

    def turn_acknowledged(self, task, meta):
        if not meta.get("busy_gen"):
            return False
        return (
            self.run(
                "bash",
                "-c",
                """set -eu
. "$1/fm-busy-lib.sh"
[ "$(fm_busy_current_gen "$2" "$3")" = "$5" ]
read -r _ source _ _ <<< "$(fm_busy_record_read "$2" "$3")"
case "$source" in ''|fm-*) exit 1 ;; esac
fm_busy_source_trusted "$4" "$source"
""",
                "bash",
                SCRIPTS,
                self.state,
                task,
                meta["harness"],
                meta["busy_gen"],
                check=False,
            ).returncode
            == 0
        )

    def resume(self):
        if not self.receipt:
            legacy = next(iter(sorted(self.state.glob("*.meta"))), None)
            if legacy:
                raise ValueError(
                    f"{legacy.stem}: no fullstop receipt for the original tmux socket/server and worker stop. Reconnect the original server and run fullstop first; an already-discarded legacy server cannot be reconstructed from task metadata"
                )
            return
        if self.receipt["phase"] == "complete":
            return
        token = self.receipt["token"]
        server = self.server()
        replacement = self.receipt["server"] != server and server is not None
        if replacement:
            if (
                self.receipt["phase"] != "resuming"
                or self.tmux("show-options", "-gqv", "@fm-vessel-resume") != token
            ):
                raise ValueError(
                    f"tmux socket path reused by a different server: {self.socket}"
                )
            original = self.receipt["server"]
            if (
                original
                and original[0] == self.boot
                and identity(original[1][0]) == original[1]
            ):
                raise ValueError(
                    "original tmux server still exists; refusing to resume on its replacement"
                )
        else:
            self.check_original_server(server)
        panes = self.panes(server)
        for task, entry in self.receipt["tasks"].items():
            self.check_task(task, entry, self.metadata(task))
        self.receipt["phase"] = "resuming"
        self.save()
        if self.receipt["restart_validation"] and not self.daemon_running():
            self.run("no-mistakes", "daemon", "start")
            deadline = time.monotonic() + 15
            while not self.daemon_running():
                if time.monotonic() >= deadline:
                    raise ValueError("vessel validation daemon did not become healthy")
                time.sleep(0.25)
        for task, entry in self.receipt["tasks"].items():
            if entry["validation_wait"]:
                continue
            endpoint = entry["identity"]["endpoint"]
            current = panes.get(endpoint, [])
            attempt = self.metadata(task).get("control_relaunch_tx") == token
            if current:
                known = len(current) == 1 and (
                    self.tmux(
                        "show-options", "-wqv", "-t", current[0][0], "@fm-vessel-resume"
                    )
                    == token
                    if replacement
                    else current[0][:3] == entry["pane"]
                )
                if not known:
                    raise ValueError(
                        f"{task}: endpoint {endpoint} is not the checkpointed or recovery-owned pane"
                    )
                state = self.agent_state(endpoint)
                if state == "alive" and entry["outcome"] == "pending" and not attempt:
                    print(
                        f"{task}: left the original live worker intact", file=sys.stderr
                    )
                    continue
                if state == "alive" and attempt:
                    self.wait_for_turn(task)
                    continue
                if state != "dead" or attempt:
                    raise ValueError(
                        f"{task}: endpoint {endpoint} reads {state}; refusing to repeat an uncertain launch or stop"
                    )
            else:
                if (
                    entry["outcome"] != "stopped"
                    or attempt
                    or (server is not None and not replacement)
                ):
                    raise ValueError(
                        f"{task}: missing endpoint {endpoint} without a verified stop for this launch"
                    )
                if server is None:
                    self.tmux(
                        "new-session",
                        "-d",
                        "-s",
                        "firstmate",
                        "-c",
                        self.options["FM_HOME"],
                        ";",
                        "set-option",
                        "-g",
                        "@fm-vessel-resume",
                        token,
                    )
                    server = self.server()
                    replacement = True
                session, window = endpoint.split(":", 1)
                if session != "firstmate":
                    raise ValueError(
                        f"{task}: cannot recreate non-default vessel session {session}"
                    )
                self.tmux(
                    "new-window",
                    "-d",
                    "-t",
                    "firstmate:",
                    "-n",
                    window,
                    "-c",
                    entry["identity"]["worktree"],
                    ";",
                    "set-option",
                    "-w",
                    "-t",
                    f"=firstmate:={window}",
                    "@fm-vessel-resume",
                    token,
                    ";",
                    "set-option",
                    "-w",
                    "-t",
                    f"=firstmate:={window}",
                    "automatic-rename",
                    "off",
                )
            self.env["FM_VESSEL_RELAUNCH_TX"] = token
            result = self.run(
                SCRIPTS / "fm-control.sh", task, "relaunch", "--note", NOTE, check=False
            )
            if result.returncode:
                raise ValueError(
                    f"{task}: relaunch did not complete; retry resume to reconcile the original endpoint and transaction"
                )
            self.wait_for_turn(task)
        self.receipt["phase"] = "complete"
        self.save()

    def wait_for_turn(self, task):
        deadline = time.monotonic() + 30
        while True:
            meta = self.metadata(task)
            if meta.get("control_relaunch_tx") == self.receipt[
                "token"
            ] and self.turn_acknowledged(task, meta):
                print(
                    f"{task}: worker began processing its recovered instructions",
                    file=sys.stderr,
                )
                return
            if time.monotonic() >= deadline:
                raise ValueError(
                    f"{task}: replacement was published but no native turn was confirmed; inspect the existing worker, do not dispatch a duplicate"
                )
            time.sleep(0.25)
