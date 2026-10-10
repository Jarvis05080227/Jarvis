"""Public-repo stub of the private isolation pad.

In the private tree this module redirects every production ledger
(memory_pool/*.jsonl, *.db) to a temp dir before a test runs. The public
repo ships no production data, so the pad is a no-op; it exists only so the
original test files import unchanged.
"""
import os

os.environ.setdefault('JARVIS_TEST_MODE', '1')
