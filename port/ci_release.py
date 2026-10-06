#!/usr/bin/env python3
"""Create a GitHub Release and upload (large) ROM assets -- from this box.

Why not `gh`?
-------------
`gh` is not installed here, and installing it would not help: it authenticates
its own way and this repo already has a working credential in the platform
store. So we talk to the REST API directly.

Why not `requests`?
-------------------
It is not installed in the managed interpreter either. JSON calls use stdlib
`urllib`; the big binary upload shells out to `curl` with its config read from
**stdin** (`curl -K -`), so the bearer token never appears in `argv` (where any
process on the box could read it) and never in the shell history.

The token itself comes from `port/ci_auth.py`, which is the one place that
knows how to dig it out of wincred/osxkeychain without hanging. Nothing here
prints it.

Usage
-----
    python port/ci_release.py list
    python port/ci_release.py publish --tag wifi64-v1.0 \\
        --name "Wi-Fi 版 64 位整合包 v1.0" --notes-file NOTES.md \\
        --asset "WiFi版64位整合包.zip" "WiFi版64位整合包.zip.sha256"
    python port/ci_release.py publish --tag T ... --dry-run   # no network writes

Exit status mirrors the operation (0 ok, non-zero otherwise).
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ci_auth  # noqa: E402

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"


def repo_slug(remote="origin"):
    """owner/repo for the given remote, parsed from its https URL."""
    url = subprocess.run(["git", "remote", "get-url", remote],
                         capture_output=True, text=True, check=True).stdout.strip()
    # https://github.com/owner/repo.git  or  git@github.com:owner/repo.git
    path = url.split("github.com", 1)[-1].lstrip(":/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        raise SystemExit("cannot parse owner/repo from %r" % url)
    return "%s/%s" % (parts[-2], parts[-1])


def api(method, url, token, data=None):
    """JSON API call -> (status, payload). Never raises on HTTP errors."""
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "archytas-ci-release")
    if token:
        req.add_header("Authorization", "Bearer %s" % token)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, raw.decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        return 0, "network error: %s" % exc.reason


def _explain(status, payload):
    if isinstance(payload, dict):
        msg = payload.get("message") or payload.get("error") or ""
        errs = payload.get("errors")
        if errs:
            msg += " " + json.dumps(errs, ensure_ascii=False)
        return "%s %s" % (status, msg)
    return "%s %s" % (status, payload)


def cmd_list(args, slug, token):
    status, payload = api("GET", "%s/repos/%s/releases?per_page=100" % (API, slug), token)
    if status != 200:
        raise SystemExit("list failed: " + _explain(status, payload))
    if not payload:
        print("(no releases)")
        return 0
    for rel in payload:
        assets = rel.get("assets") or []
        print("%-18s %-9s %s" % (rel.get("tag_name"),
                                 "draft" if rel.get("draft") else
                                 ("pre" if rel.get("prerelease") else "release"),
                                 rel.get("name") or ""))
        for a in assets:
            print("    - %-40s %10d B  %s" % (a.get("name"), a.get("size", 0),
                                              a.get("browser_download_url", "")))
    return 0


def get_release(slug, tag, token):
    status, payload = api("GET", "%s/repos/%s/releases/tags/%s" % (API, slug, tag), token)
    return payload if status == 200 else None


def create_release(slug, args, token):
    data = {"tag_name": args.tag, "name": args.name, "draft": args.draft,
            "prerelease": args.prerelease, "generate_release_notes": False}
    if args.notes is not None:
        data["body"] = args.notes
    if args.target:
        data["target_commitish"] = args.target
    status, payload = api("POST", "%s/repos/%s/releases" % (API, slug), token, data)
    if status not in (200, 201):
        raise SystemExit("create release failed: " + _explain(status, payload))
    return payload


def upload_asset(slug, release, path, token, asset_name=None):
    """Upload one file as a release asset via curl, token fed over stdin.

    Two Windows landmines are handled here:

    * `ssl-no-revoke` -- Windows curl uses the **schannel** backend, which
      hard-fails the TLS handshake when it cannot reach the certificate
      revocation endpoints: `CRYPT_E_NO_REVOCATION_CHECK (0x80092012)`.
      On a network that blocks CRL/OCSP that kills *every* github.com request
      with `HTTP 000` in under a tenth of a second. Python's urllib is
      unaffected (it uses OpenSSL, which does not make revocation mandatory),
      which is why the JSON API works while curl does not. The flag is
      schannel-only, so it is added on Windows only.
    * the config bytes are written as **UTF-8** and the output is decoded with
      `errors="replace"` -- passing `text=True` lets Windows encode stdin with
      the locale codepage (GBK here), and lets a stray non-UTF-8 byte from
      curl kill the reader thread with UnicodeDecodeError instead of being
      reported.
    """
    name = asset_name or os.path.basename(path)
    size = os.path.getsize(path)
    url = "%s/repos/%s/releases/%s/assets?name=%s" % (
        UPLOADS, slug, release["id"], urllib.parse.quote(name))
    lines = []
    if os.name == "nt":
        lines.append("ssl-no-revoke")
    lines += [
        'header = "Authorization: Bearer %s"' % token,
        'header = "Accept: application/vnd.github+json"',
        'header = "X-GitHub-Api-Version: 2022-11-28"',
        'header = "Content-Type: application/octet-stream"',
        'user-agent = "archytas-ci-release"',
        'request = "POST"',
        'url = "%s"' % url,
        'upload-file = "%s"' % path.replace("\\", "/"),
        # `silent` mutes the progress meter; the response BODY still reaches
        # stdout. `fail-with-body` turns any non-2xx into a non-zero exit
        # while keeping that body, so returncode alone is the verdict -- no
        # fragile `write-out` `%`-escaping to get wrong.
        "silent",
        "show-error",
        "fail-with-body",
    ]
    cfg = "\n".join(lines) + "\n"

    print("  uploading %s (%.1f MiB) ..." % (name, size / 1048576.0), flush=True)
    proc = subprocess.run(["curl", "-K", "-"], input=cfg.encode("utf-8"),
                          capture_output=True)
    out = (proc.stdout or b"").decode("utf-8", "replace").strip()
    err = (proc.stderr or b"").decode("utf-8", "replace").strip()
    if proc.returncode != 0:
        print("  curl exit=%d" % proc.returncode, file=sys.stderr)
    hint = (err or out).splitlines()
    hint = hint[-1] if hint else "?"
    ok = proc.returncode == 0
    if ok:
        try:
            meta = json.loads(out)
            hint = "%s -> %s" % (meta.get("name"), meta.get("browser_download_url"))
        except Exception:
            pass
    print("  -> %s (%s)" % ("OK" if ok else "FAILED", hint))
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-r", "--remote", default="origin")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list existing releases")

    p = sub.add_parser("publish", help="create a release and upload assets")
    p.add_argument("--tag", required=True)
    p.add_argument("--name", default=None, help="release title (default: the tag)")
    p.add_argument("--notes", default=None, help="release body text")
    p.add_argument("--notes-file", default=None, help="read release body from a file")
    p.add_argument("--target", default=None, help="branch/sha to tag (default: repo default)")
    p.add_argument("--prerelease", action="store_true")
    p.add_argument("--draft", action="store_true")
    p.add_argument("--asset", nargs="*", default=[],
                   help="files to upload; LOCAL or LOCAL=NAME to set the "
                        "published asset name (GitHub rewrites non-ASCII names)")
    p.add_argument("--dry-run", action="store_true")

    d = sub.add_parser("delete-asset", help="delete one asset from a release")
    d.add_argument("--tag", required=True)
    d.add_argument("--name", required=True, help="the ASSET name as GitHub stored it")

    args = ap.parse_args()

    if args.cmd == "publish" and args.notes_file:
        with open(args.notes_file, "r", encoding="utf-8") as fh:
            args.notes = fh.read()

    slug = repo_slug(args.remote)
    token = ci_auth.token(verbose=True)
    print("repo: %s" % slug)
    if not token:
        raise SystemExit("no GitHub token -- cannot write releases")

    if args.cmd == "list":
        return cmd_list(args, slug, token)

    if args.cmd == "delete-asset":
        rel = get_release(slug, args.tag, token)
        if not rel:
            raise SystemExit("no release for tag %s" % args.tag)
        for a in (rel.get("assets") or []):
            if a["name"] == args.name:
                status, payload = api(
                    "DELETE", "%s/repos/%s/releases/assets/%s" % (API, slug, a["id"]),
                    token)
                if status != 204:
                    raise SystemExit("delete failed: " + _explain(status, payload))
                print("deleted asset %s" % args.name)
                return 0
        print("asset %r not found on %s" % (args.name, args.tag))
        return 0

    # publish
    args.name = args.name or args.tag
    specs = []
    for spec in args.asset:
        local, _, aname = spec.partition("=")
        specs.append((local, aname or os.path.basename(local)))
    if args.dry_run:
        print("dry-run: would create release tag=%s name=%r draft=%s prerelease=%s"
              % (args.tag, args.name, args.draft, args.prerelease))
        for local, aname in specs:
            print("dry-run: would upload %s -> %r (%.1f MiB)"
                  % (local, aname, os.path.getsize(local) / 1048576.0))
        return 0

    existing = get_release(slug, args.tag, token)
    if existing:
        print("release for tag %s already exists (id=%s) -- reusing"
              % (args.tag, existing["id"]))
        rel = existing
        # Keep the body/title in sync with the notes file even on a re-run.
        patch = {}
        if args.notes is not None and args.notes != (rel.get("body") or ""):
            patch["body"] = args.notes
        if args.name and args.name != rel.get("name"):
            patch["name"] = args.name
        if patch:
            status, payload = api("PATCH", "%s/repos/%s/releases/%s" % (API, slug, rel["id"]),
                                  token, patch)
            if status == 200:
                rel = payload
                print("updated release %s" % ", ".join(sorted(patch)))
            else:
                print("update failed: " + _explain(status, payload), file=sys.stderr)
    else:
        rel = create_release(slug, args, token)
        print("created release: %s" % rel.get("html_url"))

    have = {a["name"] for a in (rel.get("assets") or [])}
    failed = []
    for local, aname in specs:
        if aname in have:
            print("  skip %s (already uploaded)" % aname)
            continue
        if not upload_asset(slug, rel, local, token, asset_name=aname):
            failed.append(aname)

    print("release URL: %s" % rel.get("html_url"))
    if failed:
        raise SystemExit("failed assets: %s" % ", ".join(failed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
