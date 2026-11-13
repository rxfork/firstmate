"""Interrupt the real initialization CLI at filesystem publication boundaries."""

import os
import runpy
import sys
from pathlib import Path

phase = os.environ.get("FM_TEST_INIT_INTERRUPT")
original_mkdir = Path.mkdir
original_link = os.link
original_replace = os.replace


def mkdir(path, *args, **kwargs):
    if phase == "before-payload" and path == Path(os.environ["FM_HOME"]):
        raise KeyboardInterrupt
    return original_mkdir(path, *args, **kwargs)


def link(source, destination, *args, **kwargs):
    result = original_link(source, destination, *args, **kwargs)
    if (
        phase == "after-backend"
        and Path(destination) == Path(os.environ["FM_CONFIG_OVERRIDE"]) / "backend"
    ):
        raise KeyboardInterrupt
    return result


def replace(source, destination, *args, **kwargs):
    result = original_replace(source, destination, *args, **kwargs)
    if (
        phase == "after-record"
        and Path(destination)
        == Path(os.environ["FM_ANCHOR"]) / ".firstmate/flagship.json"
    ):
        raise KeyboardInterrupt
    return result


Path.mkdir = mkdir
os.link = link
os.replace = replace
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
