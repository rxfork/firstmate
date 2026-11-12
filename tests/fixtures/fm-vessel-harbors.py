#!/usr/bin/env python3
"""Serial SSH/rsync fixtures: each simulated harbor sees the same anchor path.

Only fixture directories are exchanged; SSH executes the real peer command and
rsync copies real files. No production hostname or filesystem test hooks.
"""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

world = Path(os.environ["FM_FIXTURE_WORLD"])
anchor = world / "anchor with spaces"
operation, *args = sys.argv[1:]
if operation == "uname":
    if args == ["-n"]:
        print(os.environ["FM_FIXTURE_HARBOR"])
    else:
        subprocess.run(["/usr/bin/uname", *args], check=True)
elif operation == "ssh":
    host, command = args[args.index("--") + 1 :]
    peer_operation = shlex.split(command)[-1]
    with (world / "calls").open("a") as log:
        log.write(host + "\n")
    host = "desktop" if host == "work" else host
    slot = world / "hosts" / host / "anchor"
    if not slot.is_dir():
        sys.exit(255)
    if (world / (host + ".fail-commit")).exists() and peer_operation == "commit":
        sys.exit(255)
    if (world / (host + ".change-source")).exists() and peer_operation == "prepare":
        path = slot / ".firstmate.json"
        record = json.loads(path.read_text())
        record["known_harbors"].append("unseen")
        path.write_text(json.dumps(record))
    current = world / "hosts" / os.environ["FM_FIXTURE_HARBOR"] / "anchor"
    anchor.rename(current)
    slot.rename(anchor)
    home = world / "hosts" / host / "home"
    try:
        result = subprocess.run(
            ["bash", "-c", command],
            check=False,
            env={
                **os.environ,
                "FM_FIXTURE_HARBOR": host,
                "HOME": str(home),
                "XDG_DATA_HOME": str(home / ".local/share"),
            },
        )
    finally:
        anchor.rename(slot)
        current.rename(anchor)
    if (world / (host + ".fail-commit-after")).exists() and peer_operation == "commit":
        sys.exit(255)
    sys.exit(result.returncode)
elif operation == "rsync":
    if (world / "fail-copy").exists():
        sys.exit(23)
    source = args[-2]
    host, remote = source.split(":", 1)
    host = "desktop" if host == "work" else host
    relative = Path(remote).relative_to(anchor)
    args[-2] = str(world / "hosts" / host / "anchor" / relative) + "/"
    result = subprocess.run([os.environ["FM_REAL_RSYNC"], *args], check=False)
    sys.exit(result.returncode)
else:
    raise SystemExit("unknown fixture operation")
