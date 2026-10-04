#!/usr/bin/env python3
"""Trigger the Archytas build workflow (workflow_dispatch) and report the run.

    python3 port/ci_dispatch.py                  # full build, both boot stages
    python3 port/ci_dispatch.py --modules-only   # fast lane: only the 4 .ko (~3 min)
    python3 port/ci_dispatch.py --stage stage2   # Wi-Fi-DTB boot only

Why this is a script rather than a bash one-liner: `gh` is not installed on this
box, and workflow_dispatch needs a token anyway -- port/ci_auth.py already
supplies one without hanging on the WorkBuddy credential selector. The token is
never printed.

The dispatch endpoint answers 204 with an EMPTY body, so there is no run id to
report here. This script therefore also resolves the run it just created (the
newest run whose head_sha matches the checked-out HEAD) and prints its URL, so
`ci_logs.py --run <id>` can be pointed at it immediately.
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ci_auth import token as _token        # noqa: E402

REPO = "victormc-dev/archytas-kernel-64"
API = "https://api.github.com/repos/" + REPO
WORKFLOW = "build.yml"


def _req(path, tok, data=None, method=None):
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "archytas-ci-dispatch")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if tok:
        req.add_header("Authorization", "token " + tok)
    with urllib.request.urlopen(req, timeout=90) as resp:
        return resp.status, resp.read()


def head_sha():
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                           text=True, timeout=20)
        return r.stdout.strip() or None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modules-only", action="store_true",
                    help="fast lane: skip the kernel build, only the 4 .ko")
    ap.add_argument("--stage", default="both", choices=("both", "stage1", "stage2"))
    ap.add_argument("--ref", default="main")
    args = ap.parse_args()

    tok = _token()
    print("auth: %s" % ("token" if tok else "anonymous"))
    if not tok:
        print("ERROR: workflow_dispatch requires a token", file=sys.stderr)
        return 1

    body = json.dumps({
        "ref": args.ref,
        "inputs": {
            "modules_only": "true" if args.modules_only else "false",
            "build_stage": args.stage,
        },
    }).encode()
    try:
        status, _ = _req("/actions/workflows/%s/dispatches" % WORKFLOW, tok,
                         data=body, method="POST")
    except urllib.error.HTTPError as e:
        print("ERROR: dispatch failed: HTTP %s %s" % (e.code, e.read()[:300]))
        return 1
    print("dispatch: HTTP %s (204 == accepted), modules_only=%s stage=%s ref=%s"
          % (status, args.modules_only, args.stage, args.ref))

    # Resolve the run we just created. It can take a moment to appear.
    import time
    sha = head_sha()
    for _ in range(12):
        try:
            _, raw = _req("/actions/runs?per_page=10", tok)
            runs = json.loads(raw).get("workflow_runs", [])
        except Exception as e:
            print("   (could not list runs yet: %s)" % e)
            runs = []
        match = next((r for r in runs
                      if r["event"] == "workflow_dispatch"
                      and (sha is None or r["head_sha"] == sha)), None)
        if match:
            print("run #%s  sha=%s  %s" % (match["id"], match["head_sha"][:9],
                                           match["html_url"]))
            print("   watch: python3 port/ci_logs.py --run %s" % match["id"])
            return 0
        time.sleep(5)
    print("   NOTE: run not visible yet; use `ci_logs.py --list` (sha=%s)"
          % (sha or "?"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
