"""Pytest bootstrap: put the main/ source root on sys.path so tests import
the components the same way the runtime does (bot, controller, config)."""

import os
import sys

sys.dont_write_bytecode = True  # keep the tree free of __pycache__
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "main"))
