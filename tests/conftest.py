"""Minimal conftest for the public repo.

1. Put core/ on sys.path so the tests import the shipped modules directly.
2. Skip the handful of tests that are wiring anchors into the private tree
   (they open CLI scripts / modules that this repo does not ship). Each one is
   listed with the private file it needs, so the skip is visible, not silent.
"""
import os
import sys

import pytest

_CORE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'core')
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)
os.environ.setdefault('JARVIS_TEST_MODE', '1')

# test id (substring) -> private-tree file it needs
PRIVATE_WIRING = {
    '_test_log_fold.py::TestFoldAlert::test_b5_call_sites_wired': 'call sites in private modules',
    '_test_cfg_split_1_config_overlay.py::T6AdoptGuard': 'scripts/config_overlay_dump.py',
    '_test_cfg_split_1_config_overlay.py::T8RealModule': 'jarvis_ask.py',
    '_test_cfg_split_1_config_overlay.py::T9GenericCliRouting': 'scripts/vocab_dump.py',
    '_test_cfg_split_1_config_overlay.py::T10NewKeyGuard': 'scripts/config_overlay_dump.py',
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        for key, need in PRIVATE_WIRING.items():
            if key in item.nodeid.replace(chr(92), '/'):
                item.add_marker(pytest.mark.skip(reason='private-tree wiring anchor, needs ' + need))
