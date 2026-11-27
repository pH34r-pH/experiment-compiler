"""Optional dependencies used by offline integration fixtures."""
from importlib.util import find_spec
from unittest import skipUnless

HAS_RUNNER = find_spec("yaml") is not None
REQUIRES_RUNNER = skipUnless(HAS_RUNNER, "install the runner extra for CWL/YAML tests")
