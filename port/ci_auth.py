#!/usr/bin/env python3
"""Supply a GitHub token to the CI helper scripts -- without ever hanging.

This is its own module because BOTH port/ci_logs.py and port/ci_artifact.py need
it, and getting it wrong hangs the caller forever, twice over:

  1. The credential helper list can START with an interactive helper. On this
     Windows box it is WorkBuddy's `helper-selector`, which blocks instead of
     answering, so `git credential fill` never returns at all.
  2. `subprocess.run(..., capture_output=True, timeout=N)` only kills the DIRECT
     child. The helper that child spawned survives, keeps the inherited stdout
     pipe open, and `communicate()` then waits on it indefinitely -- so the
     advertised `timeout` does not actually bound anything. Symptom: a script
     that should take seconds sits there for ten minutes.

Both are fixed here:

  * every attempt runs with stdout/stderr redirected to temp FILES, so there is
    no pipe left to drain and a straggler cannot block the caller;
  * it is bounded by `timeout` anyway;
  * the platform credential store is tried FIRST -- `wincred` on Windows,
    `osxkeychain` on macOS -- because it holds the very same credential and
    answers immediately, while plain `git credential fill` stays as the last
    resort so a host that relies on GCM/manager-core still works.

The token is only ever returned to the caller. It is never printed.
"""
import os
import shutil
import subprocess
import sys
import tempfile

QUERY = "protocol=https\nhost=github.com\n\n"

# Fast, non-interactive stores first; plain `git credential fill` last.
if os.name == "nt":
    PLATFORM_HELPERS = ("wincred", "store")
else:
    PLATFORM_HELPERS = ("osxkeychain", "store")


def _attempt(cmd, timeout):
    """Run one credential command; return the password or None.

    File redirection rather than pipes is the whole point -- see the module
    docstring. On timeout we simply return: the temp files are closed by the
    `with` block and nothing is waiting on a pipe.
    """
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as out, \
         tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as err:
        try:
            subprocess.run(cmd, input=QUERY, stdout=out, stderr=err,
                           text=True, timeout=timeout)
        except Exception:
            return None
        out.seek(0)
        for line in out.read().splitlines():
            if line.startswith("password="):
                return line.split("=", 1)[1].strip() or None
    return None


def _attempts():
    cmds = []
    for h in PLATFORM_HELPERS:
        if shutil.which("git-credential-" + h):
            # An empty credential.helper first RESETS the configured list, so the
            # blocking helper never runs on this attempt.
            cmds.append(["git", "-c", "credential.helper=",
                         "-c", "credential.helper=" + h, "credential", "fill"])
    cmds.append(["git", "credential", "fill"])
    return cmds


def token(timeout=15, verbose=False):
    """Return a GitHub token, or None to fall back to anonymous access."""
    for cmd in _attempts():
        pw = _attempt(cmd, timeout)
        if pw:
            if verbose:
                print("auth: token via `%s`" % " ".join(cmd), file=sys.stderr)
            return pw
    if verbose:
        print("auth: anonymous -- no stored credential answered within %ds"
              % timeout, file=sys.stderr)
    return None


if __name__ == "__main__":
    t = token(verbose=True)
    print("token: %s" % ("<present, %d chars>" % len(t) if t else "none"))
