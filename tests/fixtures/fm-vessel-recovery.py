"""tmux and lifecycle tool seam for portable vessel transaction fault injection."""

import json
import os
import signal
import socket
import sys
import time
from pathlib import Path

mode, *args = sys.argv[1:]
world = Path(os.environ["FM_TEST_RECOVERY_WORLD"])
state = Path(os.environ["FM_STATE_OVERRIDE"])
file = world / "tmux.json"
server = json.loads(file.read_text())
sock = Path(os.environ["TMUX_TMPDIR"]) / f"tmux-{os.getuid()}" / "default"


def bind():
    sock.parent.mkdir(parents=True, exist_ok=True)
    with socket.socket(socket.AF_UNIX) as stream:
        stream.bind(str(sock))


def save():
    file.write_text(json.dumps(server))


if mode == "tmux":
    command = args[2]
    panes = server["panes"]
    if command == "display-message":
        print(server["pid"])
    elif command == "list-panes":
        if args[-1].startswith("#{pid}"):
            for pane in panes.values():
                print(f"{server['pid']}\t{pane['pid']}\tbash")
        else:
            for name, pane in panes.items():
                print(
                    f"{name}\t@{pane['index']}\t%{pane['index']}\t{pane['pid']}\t{pane['cwd']}"
                )
    elif command == "kill-server":
        sock.unlink()
        os.kill(server["pid"], signal.SIGTERM)
        server["panes"] = {}
        save()
    elif command == "list-sessions":
        print("\n".join(server["sessions"]))
    elif command == "new-session":
        session = args[args.index("-s") + 1]
        if not sock.exists():
            bind()
            server.update(pid=server["replacement_pid"], token=args[-1], sessions=[])
        if session in server["sessions"]:
            sys.exit("duplicate session")
        server["sessions"].append(session)
        save()
    elif command == "new-window":
        session = args[args.index("-t") + 1].removeprefix("=").removesuffix(":")
        name = f"{session}:" + args[args.index("-n") + 1]
        if session not in server["sessions"] or name in panes:
            sys.exit("missing session or duplicate endpoint")
        panes[name] = {
            "index": len(panes) + 1,
            "pid": server["pane_pid"],
            "cwd": args[args.index("-c") + 1],
            "state": "dead",
            "token": args[args.index("@fm-vessel-resume") + 1],
        }
        save()
        if (world / "fail-window").exists():
            (world / "fail-window").unlink()
            sys.exit("fixture lost window publication acknowledgement")
    elif command == "show-options":
        if "-t" not in args:
            print(server.get("token", ""))
        else:
            index = int(args[args.index("-t") + 1][1:])
            print(next(x for x in panes.values() if x["index"] == index)["token"])
    else:
        sys.exit(f"unexpected tmux command: {args}")
elif mode == "crew":
    print(
        "state: parked · source: run-step"
        if args[0] == "parked"
        else "state: working · source: pane"
    )
elif mode == "watch":
    pass
elif mode == "agent":
    print(server["panes"][args[0]]["state"])
elif mode == "daemon":
    health = world / "validation-running"
    if args == ["daemon", "status"]:
        print("daemon running" if health.exists() else "daemon not running")
    elif args == ["daemon", "stop"]:
        health.unlink(missing_ok=True)
    elif args == ["daemon", "start"]:
        health.touch()
        with (world / "starts").open("a") as stream:
            stream.write("validation\n")
    else:
        sys.exit(f"unexpected daemon command: {args}")
elif mode == "control":
    task, verb, *_ = args
    meta = state / f"{task}.meta"
    endpoint = next(
        line.removeprefix("window=")
        for line in meta.read_text().splitlines()
        if line.startswith("window=")
    )
    pane = server["panes"][endpoint]
    if verb == "exit":
        if (world / "refuse-stop").exists() and task == "dirty":
            sys.exit("fixture refused to stop")
        pane["state"] = "dead"
        save()
        print(f"stopped {task} harness=pi")
    elif verb == "relaunch":
        with (world / "launches").open("a") as stream:
            stream.write(task + "\n")
        if (world / "block-launch").exists():
            (world / "blocked").touch()
            for _ in range(200):
                if not (world / "block-launch").exists():
                    break
                time.sleep(0.05)
            else:
                sys.exit("fixture timed out waiting to publish")
        assert pane["state"] == "dead", "relaunch would duplicate a live worker"
        with meta.open("a") as stream:
            stream.write(
                f"control_relaunch_tx={os.environ['FM_VESSEL_RELAUNCH_TX']}\nbusy_gen=test-gen\n"
            )
        (state / f"{task}.busy-gen").write_text("test-gen\n")
        (state / f"{task}.busy-state").write_text(
            "v1 gen=test-gen seq=1 state=busy source=pi-ext event=agent-start ts=1\n"
        )
        pane["state"] = "alive"
        save()
        if (world / "fail-launch").exists():
            (world / "fail-launch").unlink()
            sys.exit("fixture interrupted after publishing the replacement")
    else:
        sys.exit(f"unexpected lifecycle verb: {verb}")
