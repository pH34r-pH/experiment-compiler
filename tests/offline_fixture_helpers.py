"""Fail immediately on network or process activity inside offline operations."""

import os
import socket
import subprocess
from contextlib import ExitStack, contextmanager
from unittest.mock import patch


@contextmanager
def offline_operations():
    with ExitStack() as guards:
        for module, names in (
            (socket, ("socket", "getaddrinfo", "gethostbyname", "gethostbyname_ex")),
            (subprocess, ("Popen",)),
            (os, ("system", "posix_spawn", "posix_spawnp", "spawnv", "spawnve", "execv", "execve")),
        ):
            for name in names:
                if hasattr(module, name):
                    forbidden = AssertionError(f"Unexpected offline call to {module.__name__}.{name}")
                    guards.enter_context(patch.object(module, name, side_effect=forbidden))
        yield
