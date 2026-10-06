#!/usr/bin/env python3
"""Push to GitHub without hanging, and without leaking the token.

Self-contained: no project-specific imports. Copy into any repo (or call it
from anywhere -- it operates on the CWD's git repo).

   python git_push.py                      # HEAD -> origin, same branch
   python git_push.py -r upstream          # another remote
   python git_push.py -b main --dry-run
   python git_push.py --force-with-lease

The two problems this solves:

  1. `credential.helper` may START with an INTERACTIVE helper (WorkBuddy's
     `helper-selector` on Windows). It blocks on a UI prompt that a
     non-interactive shell can never answer, so `git push` never returns.
     Fix: try the platform store first (`wincred` / `osxkeychain`), and pass
     an EMPTY `credential.helper` first to reset the configured list so the
     interactive helper never runs.

  2. `subprocess.run(..., capture_output=True, timeout=N)` does NOT bound the
     call: `timeout` kills only the direct child, the helper it spawned keeps
     the inherited stdout pipe open, and `communicate()` waits on it forever.
     Fix: redirect to temp FILES -- there is no pipe left to drain.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

QUERY = "protocol=https\nhost=github.com\n\n"

if os.name == "nt":
    PLATFORM_HELPERS = ("wincred", "store")
else:
    PLATFORM_HELPERS = ("osxkeychain", "store")


def _attempt(cmd, timeout):
    """Run one credential command; return the password or None."""
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


def token(timeout=15, verbose=False):
    """Return a GitHub token, or None to fall back to anonymous access."""
    cmds = []
    for h in PLATFORM_HELPERS:
        if shutil.which("git-credential-" + h):
            # Empty helper first RESETS the list, so a blocking helper is skipped.
            cmds.append(["git", "-c", "credential.helper=",
                         "-c", "credential.helper=" + h, "credential", "fill"])
    cmds.append(["git", "credential", "fill"])
    for cmd in cmds:
        pw = _attempt(cmd, timeout)
        if pw:
            if verbose:
                print("auth: token via `%s`" % " ".join(cmd), file=sys.stderr)
            return pw
    if verbose:
        print("auth: anonymous -- nothing answered within %ds" % timeout,
              file=sys.stderr)
    return None


def _redact(text, secret):
    """Blank the token, and the whole userinfo field, in every line."""
    if not text:
        return text
    if secret:
        text = text.replace(secret, "***")
    out = []
    for line in text.splitlines():
        if "@github.com" in line and "://" in line:
            head, _, tail = line.partition("://")
            line = head + "://" + tail[tail.find("@github.com") + 1:]
        out.append(line)
    return "\n".join(out)


def _run(cmd, secret):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    for stream, is_err in ((proc.stdout, False), (proc.stderr, True)):
        cleaned = _redact(stream, secret)
        if cleaned and cleaned.strip():
            print(cleaned, file=sys.stderr if is_err else sys.stdout)
    return proc.returncode


def remote_url(remote, secret):
    url = subprocess.run(["git", "remote", "get-url", remote],
                         capture_output=True, text=True, check=True).stdout.strip()
    scheme, sep, rest = url.partition("://")
    if not sep:
        raise SystemExit("remote %r is not an https URL: %s" % (remote, url))
    if "@" in rest.split("/", 1)[0]:
        rest = rest.split("@", 1)[1]
    if secret:
        return "%s://x-access-token:%s@%s" % (scheme, secret, rest)
    return url


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-r", "--remote", default="origin")
    ap.add_argument("-b", "--branch", default=None,
                    help="branch to push (default: the checked-out branch)")
    ap.add_argument("--force-with-lease", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    branch = args.branch or subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()
    if branch == "HEAD":
        raise SystemExit("detached HEAD -- pass --branch explicitly")

    secret = token(verbose=True)
    url = remote_url(args.remote, secret)

    cmd = ["git", "-c", "credential.helper=",
           "push", url, "%s:%s" % (branch, branch)]
    if args.force_with_lease:
        cmd.append("--force-with-lease")

    if args.dry_run:
        print("dry-run: would push %s -> %s (URL redacted)" % (branch, args.remote))
        return 0
    return _run(cmd, secret)


if __name__ == "__main__":
    sys.exit(main())
