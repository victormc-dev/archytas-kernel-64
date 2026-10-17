#!/usr/bin/env python3
"""Push a commit to GitHub through the REST API when the git transport is down.

Usage:
    python3 port/ci_api_push.py [branch]

Why this exists
---------------
This box reaches GitHub through a local proxy that intermittently refuses
github.com's git endpoints (CONNECT tunnel failed, response 502) while
api.github.com keeps working. `git push` then fails outright -- and the
project's ci_push.py exists because a plain push HANGS here, so a retry loop
is not obviously better. The Git Database API (blobs -> tree -> commit ->
ref update) goes through api.github.com and achieves the same result.

A ref update through the API counts as a push for webhooks/Actions, so the
build workflow triggers exactly as it would for `git push`.

Byte-format notes (measured, 2026-10-17)
----------------------------------------
* GitHub PRESERVES the timezone offsets that were sent (a +0800 committer
  stays +0800) -- but it STRIPS the trailing newline from the commit message.
  The API's ISO dates are displayed in UTC, which makes this hard to see.
* Consequence: the commit created through the API is byte-identical in
  content (same tree, parents, message) but has a DIFFERENT SHA than the
  local commit. After a successful push this script rebuilds the remote
  commit object locally (same tz, message without the trailing newline),
  verifies it hashes to the SHA the API reported, and fast-forwards the
  local branch to it -- so local and remote stay in sync without a fetch.
* The remote head must be the local HEAD's parent, otherwise the script
  refuses (no force pushes; retry ci_push.py once the proxy recovers).
"""
import base64
import datetime
import json
import subprocess
import sys
import urllib.request

sys.path.insert(0, __file__.rsplit("/", 1)[0].rsplit("\\", 1)[0])
import ci_auth  # noqa: E402

API = "https://api.github.com"


def git(*args, **kw):
    return subprocess.run(["git"] + list(args), capture_output=True, text=True,
                          check=True, **kw).stdout.strip()


def api(method, path, token, body=None):
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "token " + token,
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode() or "null")


def field(fmt):
    return git("log", "-1", "--format=" + fmt)


def to_git_date(iso):
    """2026-10-17T12:11:41+08:00 -> '1792210301 +0800' (offset preserved)."""
    dt = datetime.datetime.fromisoformat(iso)
    epoch = int(dt.timestamp())
    off = dt.utcoffset()
    sign = "+" if off >= datetime.timedelta(0) else "-"
    mins = abs(int(off.total_seconds() // 60))
    return "%d %s%02d%02d" % (epoch, sign, mins // 60, mins % 60)


def align_local(remote_sha):
    """Rebuild the remote commit object locally and fast-forward to it.

    GitHub keeps the timezone offsets but drops the message's trailing
    newline, so the local commit object differs by exactly that. If the
    rebuilt object does NOT hash to remote_sha the format assumption broke:
    warn loudly and leave the local branch alone (a later fetch will fix it).
    """
    msg = git("log", "-1", "--format=%B").rstrip("\n")
    body = ("tree %s\nparent %s\n"
            "author %s <%s> %s\n"
            "committer %s <%s> %s\n\n%s"
            % (field("%T"), field("%P"), field("%an"), field("%ae"),
               to_git_date(field("%aI")), field("%cn"), field("%ce"),
               to_git_date(field("%cI")), msg))
    sha = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"],
                         input=body.encode(), capture_output=True,
                         check=True).stdout.decode().strip()
    if sha != remote_sha:
        print("WARNING: rebuilt %s != remote %s -- local branch left as is; "
              "run a real fetch when github.com is reachable" % (sha, remote_sha))
        return False
    git("reset", "--hard", sha)
    git("update-ref", "refs/remotes/origin/main", sha)
    print("aligned: local main == remote main (%s)" % sha[:10])
    return True


def main():
    branch = sys.argv[1] if len(sys.argv) > 1 else "main"
    repo = git("remote", "get-url", "origin")
    # https://github.com/<owner>/<repo>.git
    owner_repo = repo.split("github.com/", 1)[1].rstrip("/").removesuffix(".git")
    print("repo: %s  branch: %s" % (owner_repo, branch))

    token = ci_auth.token()
    if not token:
        sys.exit("ERROR: could not retrieve a GitHub token")

    head = api("GET", "/repos/%s/git/ref/heads/%s" % (owner_repo, branch), token)
    remote_sha = head["object"]["sha"]
    local_sha = git("rev-parse", "HEAD")
    parent = git("rev-parse", "HEAD^")
    print("remote %s = %s" % (branch, remote_sha[:10]))
    print("local  HEAD = %s (parent %s)" % (local_sha[:10], parent[:10]))
    if remote_sha != parent:
        sys.exit("ERROR: remote %s is not the local HEAD's parent (%s); "
                 "refusing to force -- retry the normal push path instead"
                 % (branch, parent[:10]))

    files = git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()
    print("files in commit: %s" % ", ".join(files))
    entries = []
    for path in files:
        raw = open(path, "rb").read()
        blob = api("POST", "/repos/%s/git/blobs" % owner_repo, token, {
            "content": base64.b64encode(raw).decode(),
            "encoding": "base64"})
        entries.append({"path": path, "mode": "100644",
                        "type": "blob", "sha": blob["sha"]})
        print("  blob %s  %s" % (blob["sha"][:10], path))

    parent_tree = api("GET", "/repos/%s/git/commits/%s" % (owner_repo, parent),
                      token)["tree"]["sha"]
    tree = api("POST", "/repos/%s/git/trees" % owner_repo, token,
               {"base_tree": parent_tree, "tree": entries})
    print("new tree = %s (local %s)" % (tree["sha"][:10], field("%T")[:10]))
    if tree["sha"] != field("%T"):
        sys.exit("ERROR: recreated tree SHA differs from the local commit's tree")

    fmt = "%an%n%ae%n%aI%n%cn%n%ce%n%cI%n%B"
    an, ae, ad, cn, ce, cd, message = git("log", "-1", "--format=" + fmt).split("\n", 6)
    # git appends one newline after the format output; GitHub strips whatever
    # trailing newlines the message has, so send it without any.
    message = message.rstrip("\n")

    def ident(name, email, date):
        return {"name": name, "email": email, "date": date}

    commit = api("POST", "/repos/%s/git/commits" % owner_repo, token, {
        "message": message,
        "tree": tree["sha"],
        "parents": [parent],
        "author": ident(an, ae, ad),
        "committer": ident(cn, ce, cd)})
    print("new commit = %s (local %s)" % (commit["sha"][:10], local_sha[:10]))

    api("PATCH", "/repos/%s/git/refs/heads/%s" % (owner_repo, branch), token,
        {"sha": commit["sha"], "force": False})
    print(">>> PUSHED: %s -> %s (%s)" % (commit["sha"][:10], branch, owner_repo))

    if commit["sha"] != local_sha:
        align_local(commit["sha"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
