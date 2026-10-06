#!/usr/bin/env python3
"""Push to GitHub from this box WITHOUT hanging -- and without leaking the token.

Why this exists
---------------
`git push` in this working copy never returns. `git config credential.helper`
resolves to WorkBuddy's `helper-selector`, which is INTERACTIVE: instead of
answering, it blocks on a UI prompt that a non-interactive shell can never
satisfy. The push then sits there until it is killed by hand.

`port/ci_auth.py` already knows how to dig the token out of the platform
credential store without blocking (wincred/osxkeychain first, plain
`git credential fill` last, file redirection instead of pipes so a straggler
cannot hold the caller). This module does the missing half: hand that token
straight to `git push` as a one-shot URL, and reset `credential.helper` so the
blocking helper is never consulted.

Two details that matter
-----------------------
* The token is put in the URL, so it would normally be echoed by git's own
  "To https://user:token@github.com/..." line. Every line of output is passed
  through `_redact()` before printing, so the secret cannot reach the terminal
  or a log file.
* `--no-verify` is deliberately NOT passed. A rejected pre-push hook is a real
  failure and must stay visible.

Usage
-----
    python port/ci_push.py                 # push HEAD to origin, same branch
    python port/ci_push.py -r agui         # a different remote
    python port/ci_push.py --branch main --remote origin
    python port/ci_push.py --dry-run       # everything but the actual push

Exit status mirrors `git push` (0 on success, non-zero otherwise) so it can be
chained in a script.
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ci_auth  # noqa: E402


def _redact(text, secret):
    """Blank every occurrence of the token, and the whole userinfo field."""
    if not text:
        return text
    if secret:
        text = text.replace(secret, "***")
    # Belt and braces: `https://anything@github.com` -> `https://github.com`
    out = []
    for line in text.splitlines():
        if "@github.com" in line:
            head, _, tail = line.partition("://")
            if _:
                rest = tail[tail.find("@github.com") + 1:]
                line = head + "://" + rest
        out.append(line)
    return "\n".join(out)


def _run(cmd, secret, **kw):
    """Run a git command, echo its output redacted, return the exit code."""
    proc = subprocess.run(cmd, capture_output=True, text=True, **kw)
    for stream in (proc.stdout, proc.stderr):
        cleaned = _redact(stream, secret)
        if cleaned and cleaned.strip():
            print(cleaned, file=sys.stderr if stream is proc.stderr else sys.stdout)
    return proc.returncode


def remote_url(remote, secret):
    """Turn remote `name` into an authenticated https URL."""
    url = subprocess.run(["git", "remote", "get-url", remote],
                         capture_output=True, text=True, check=True).stdout.strip()
    scheme, _, rest = url.partition("://")
    if not _:
        raise SystemExit("remote %r is not an https URL: %s" % (remote, url))
    # Drop any existing userinfo before substituting our own.
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

    secret = ci_auth.token(verbose=True)
    url = remote_url(args.remote, secret)

    # Empty credential.helper FIRST resets the configured list, so
    # `helper-selector` never runs. The URL carries the credential anyway.
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
