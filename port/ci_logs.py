#!/usr/bin/env python3
"""Fetch GitHub Actions logs for this repo and surface the real failure.

Uses the credential already stored by git-credential-manager (never printed).
Stdlib only.

Usage:
    python3 port/ci_logs.py                 # newest run: failed step + failure tail
    python3 port/ci_logs.py --list          # list recent runs
    python3 port/ci_logs.py --run 12345678  # pick a run
    python3 port/ci_logs.py --tail 200      # lines of tail to print
"""
import argparse
import io
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

REPO = "victormc-dev/archytas-kernel-64"
API = "https://api.github.com/repos/" + REPO
MARKERS = re.compile(
    r"ERROR:|FAIL:|Traceback|^make.*\*\*\*|Error \d+$|error:|undefined|"
    r"no rule to make target|No such file|command not found|"
    r">> |=== |OK: |WARN|unsupported RELA|insmod",
    re.I)


def token():
    try:
        r = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, timeout=30)
        for line in r.stdout.splitlines():
            if line.startswith("password="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return None


def api(path, tok, raw=False):
    req = urllib.request.Request(API + path)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "archytas-ci-logs")
    if tok:
        req.add_header("Authorization", "token " + tok)
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = resp.read()
    return data if raw else json.loads(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="download the logs even when nothing failed -- needed "
                         "to read the self-checks (relocation profile, modinfo, "
                         "gate lines) of a GREEN run")
    ap.add_argument("--step", default="",
                    help="only these step logs (substring of the log entry name, "
                         "e.g. 'Connectivity')")
    ap.add_argument("--tail", type=int, default=120)
    ap.add_argument("--grep", default="")
    args = ap.parse_args()

    tok = token()
    print("auth: %s" % ("token (from git credential manager)" if tok else "anonymous"))

    runs = api("/actions/runs?per_page=10", tok).get("workflow_runs", [])
    if not runs:
        sys.exit("no workflow runs found")
    if args.list:
        for r in runs:
            print("  #%-12s %-9s %-28s %s" % (r["id"], r["conclusion"], r["name"][:28],
                                              r["head_sha"][:9]))
        return

    run = next((r for r in runs if str(r["id"]) == args.run), None) if args.run else None
    if run is None:
        run = next((r for r in runs if r["conclusion"] != "success"), runs[0])
    print("run #%s  conclusion=%s  sha=%s  event=%s  %s"
          % (run["id"], run["conclusion"], run["head_sha"][:9], run["event"],
             run["html_url"]))
    # Wall-clock duration is the quickest way to tell whether the fast lane
    # (modules_only=true) actually kicked in instead of a full kernel build.
    t0, t1 = run.get("run_started_at") or run["created_at"], run.get("updated_at")
    if t0 and t1:
        try:
            from datetime import datetime
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            d = datetime.strptime(t1, fmt) - datetime.strptime(t0, fmt)
            print("  duration %d min %02d s" % (d.seconds // 60, d.seconds % 60))
        except Exception:
            pass

    jobs = api("/actions/runs/%s/jobs?per_page=50" % run["id"], tok).get("jobs", [])
    bad_jobs = []
    for j in jobs:
        state = j["conclusion"] or j["status"]
        flag = "" if j["conclusion"] == "success" else "   <<< " + str(state)
        print("  job %-40s %s%s" % (j["name"][:40], state, flag))
        for s in j.get("steps", []):
            if s["conclusion"] not in ("success", "skipped", None):
                print("      step %-52s %s" % (s["name"][:52], s["conclusion"]))
        # None means the job has not finished yet -- only a real conclusion counts.
        if j["conclusion"] not in (None, "success", "skipped", "neutral"):
            bad_jobs.append(j)

    if not bad_jobs and not args.force:
        print("\n%s" % ("run still in progress - nothing has failed yet"
                        if run["conclusion"] is None else "no failing job"))
        return
    if not bad_jobs:
        print("\n(nothing failed -- --force: dumping the logs anyway)")

    print("\n== downloading logs (zip) ==")
    try:
        blob = api("/actions/runs/%s/logs" % run["id"], tok, raw=True)
    except Exception as e:
        print("   could not download logs: %s" % e)
        return
    zf = zipfile.ZipFile(io.BytesIO(blob))
    names = zf.namelist()
    print("   %d log files" % len(names))

    wanted = [n for n in names if any(j["name"].replace(" / ", "/") in n
                                      for j in (bad_jobs or jobs))]
    if args.step:
        wanted = [n for n in wanted if args.step.lower() in n.lower()]
    if not wanted:
        wanted = sorted(names, key=lambda n: zf.getinfo(n).file_size)[-3:]
    for n in wanted:
        text = zf.read(n).decode("utf-8", "replace")
        lines = text.splitlines()
        print("\n############ %s (%d lines) ############" % (n, len(lines)))
        print("---- last %d lines ----" % args.tail)
        for line in lines[-args.tail:]:
            print(line)
        if args.grep:
            print("---- matching /%s/ ----" % args.grep)
            rx = re.compile(args.grep)
            for i, line in enumerate(lines):
                if rx.search(line):
                    print("%6d| %s" % (i + 1, line))
        else:
            print("---- markers ----")
            for i, line in enumerate(lines):
                if MARKERS.search(line):
                    print("%6d| %s" % (i + 1, line))


if __name__ == "__main__":
    main()
