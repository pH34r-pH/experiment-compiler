# Frozen linear-regression lifecycle fixture

This fixture reruns the repository's existing self-contained linear-regression study through the lifecycle runner. Its source implementation, data, tests, exact result and acceptance criteria are copied byte-for-byte from `linear-regression-v1`; the wrapper only exposes the existing `reproduce.py` entrypoint as a CWL tool.

The existing result is a frozen comparison target. A new attempt records its own output and provenance; this does not turn a process exit or byte comparison into new scientific interpretation.
