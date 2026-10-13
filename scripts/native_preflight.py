"""Compatibility entrypoint for the bundled native component."""
import runpy
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
globals().update(runpy.run_module("novus_wechat.native_preflight", run_name=__name__))
