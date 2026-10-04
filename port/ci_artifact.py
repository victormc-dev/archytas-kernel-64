#!/usr/bin/env python3
"""Download a GitHub Actions artifact from this repo (token from
git-credential-manager, never printed).

    python3 port/ci_artifact.py --list                 # artifacts of the newest run
    python3 port/ci_artifact.py --run 37190391324 --list
    python3 port/ci_artifact.py --run 37190391324 --name archytas-wifi-boot-and-modules \
                                --out archytas-wifi-boot-and-modules

Artifacts expire, and the download URL 302-redirects to a signed Azure blob
host. urllib's default redirect handler COPIES the Authorization header onto the
redirected request, and the blob host then rejects it:

    HTTP Error 403: Server failed to authenticate the request. Make sure the
    value of Authorization header is formed correctly including the signature.

...which is confusing, because the signature in the URL is perfectly fine. So
we install a redirect handler that drops Authorization for the new host.
"""
import argparse
import io
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ci_auth import token as _token        # noqa: E402
import zipfile
from pathlib import Path

REPO = "victormc-dev/archytas-kernel-64"
API = "https://api.github.com/repos/" + REPO


class DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward Authorization to the redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            for k in [k for k in new.headers if k.lower() == "authorization"]:
                del new.headers[k]
        return new


OPENER = urllib.request.build_opener(DropAuthOnRedirect)


def token():
    # Delegated to ci_auth: a blocking credential helper plus
    # subprocess.run(capture_output=True) leaving the helper's grandchild alive
    # on the pipe used to hang this script indefinitely. See ci_auth.py.
    return _token()


def api(path, tok, raw=False):
    req = urllib.request.Request(API + path)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "archytas-ci-artifact")
    if tok:
        req.add_header("Authorization", "token " + tok)
    # OPENER follows redirects but strips Authorization on the way (see above).
    with OPENER.open(req, timeout=300) as resp:
        data = resp.read()
    return data if raw else json.loads(data)


def human(n):
    return "%.1f MiB" % (n / 1048576.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run")
    ap.add_argument("--name")
    ap.add_argument("--out")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    tok = token()
    print("auth: %s" % ("token (from git credential manager)" if tok else "anonymous"))

    run_id = args.run
    if not run_id:
        runs = api("/actions/runs?per_page=10", tok)["workflow_runs"]
        run_id = str(runs[0]["id"])
        print("using newest run #%s (%s, %s)"
              % (run_id, runs[0]["head_sha"][:9], runs[0]["conclusion"]))
    else:
        run = api("/actions/runs/%s" % run_id, tok)
        print("run #%s  sha=%s  conclusion=%s"
              % (run_id, run["head_sha"][:9], run["conclusion"]))

    arts = api("/actions/runs/%s/artifacts" % run_id, tok)["artifacts"]
    if not arts:
        sys.exit("no artifacts for run %s (expired or not uploaded)" % run_id)
    for a in arts:
        print("  %-38s %10d  %-10s  expired=%s"
              % (a["name"], a["size_in_bytes"], human(a["size_in_bytes"]),
                 a["expired"]))
    if args.list or not args.name:
        return

    art = next((a for a in arts if a["name"] == args.name), None)
    if art is None:
        sys.exit("artifact %r not found (see the list above)" % args.name)

    out = Path(args.out or ".").resolve()
    out.mkdir(parents=True, exist_ok=True)
    print("downloading %s (%s) -> %s" % (art["name"], human(art["size_in_bytes"]), out))
    blob = api("/actions/artifacts/%s/zip" % art["id"], tok, raw=True)
    zf = zipfile.ZipFile(io.BytesIO(blob))
    n = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        # Never let a crafted name escape the output directory.
        dest = (out / info.filename).resolve()
        if not str(dest).startswith(str(out)):
            print("   SKIP unsafe path %s" % info.filename)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(dest, "wb") as dst:
            dst.write(src.read())
        n += 1
    print("   extracted %d files" % n)
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.suffix in (".img", ".ko", ".dtb", ".dtbo"):
            print("   %-58s %10d" % (str(p.relative_to(out)), p.stat().st_size))


if __name__ == "__main__":
    main()
