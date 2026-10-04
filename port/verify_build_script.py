#!/usr/bin/env python3
"""Verify the Archytas build plumbing as it sits on disk.

Dev-time harness (some checks need the local kernel source copy and the
downloaded CI artifact; those skip cleanly when absent). Checks:

  1. `bash -n` accepts port/build_connectivity_modules.sh.
  2. no `| head` pipeline in the shell script -- it runs under
     `set -euo pipefail`, so SIGPIPE (141) from find/grep becomes a fatal
     assignment failure. This exact trap killed a CI run right after the first
     module, with no diagnostic at all.
  3. every embedded `python3 - ... <<'PY' ... PY` heredoc is valid Python.
  4. port/patch_adrp_reloc.py removes the CONFIG_ARM64_ERRATUM_843419 guard
     from real kernel source, and is idempotent.
  5. the workflow applies that patch BEFORE it builds the kernel -- otherwise
     the boot image ships with the unpatched module loader (silent: the build
     goes green and insmod still fails on the device).
  6. the `.modinfo` extractor reads name=/vermagic= out of a real .ko.

The Bash tool rewrites backslashes inside heredocs, so running snippets
straight from a shell command line is unreliable -- hence this harness.
"""
import ast
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "port" / "build_connectivity_modules.sh"
PATCHER = ROOT / "port" / "patch_adrp_reloc.py"
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
SRC = ROOT / "archimedes-kernel-64-main" / "arch" / "arm64" / "kernel" / "module.c"
ART = ROOT / "archytas-wifi-boot-and-modules" / "kernel" / "out-archytas" / "connectivity-modules"
EXE = sys.executable

failures = []


def check(name, ok, detail=""):
    print("   [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                            (" -- " + detail) if detail else ""))
    if not ok:
        failures.append(name)


text = SCRIPT.read_text(encoding="utf-8")

print("== 1. bash -n ==")
r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
check("bash syntax OK", r.returncode == 0, r.stderr.strip())

print("\n== 2. no `| head` under set -euo pipefail ==")
opts = re.search(r"^set .*pipefail.*$", text, re.M)
check("script sets pipefail", bool(opts), opts.group(0) if opts else "not found")
offenders = []
for i, ln in enumerate(text.splitlines()):
    s = ln.strip()
    # A leading-# line is a comment (this file documents the trap verbatim), and
    # a pipeline followed by `|| ...` is deliberately guarded against failure --
    # that is how the pre-existing curl|head|sed SHA lookup survives.
    if s.startswith("#") or re.search(r"^#", s):
        continue
    if not re.search(r"\|\s*head\b", s):
        continue
    if "||" in s.split("| head", 1)[1]:
        continue
    offenders.append((i + 1, s))
check("no unguarded `| head` pipeline", not offenders,
      "; ".join("line %d: %s" % o for o in offenders))

print("\n== 3. embedded python heredocs parse ==")
blocks = re.findall(r"<<'PY'\n(.*?)\nPY\n", text, re.S)
print("   found %d heredoc block(s)" % len(blocks))
for i, b in enumerate(blocks):
    try:
        ast.parse(b)
        check("block #%d parses" % i, True, "%d lines" % (b.count("\n") + 1))
    except SyntaxError as e:
        check("block #%d parses" % i, False, str(e))

print("\n== 4. patch_adrp_reloc.py against real kernel source ==")
if not SRC.exists():
    print("   SKIP (no local kernel source at %s)" % SRC)
else:
    tmp = Path(tempfile.mkdtemp())
    (tmp / "arch" / "arm64" / "kernel").mkdir(parents=True)
    target = tmp / "arch" / "arm64" / "kernel" / "module.c"
    shutil.copy2(SRC, target)
    before = SRC.read_text(encoding="utf-8", errors="surrogateescape")
    r1 = subprocess.run([EXE, str(PATCHER), str(tmp)], capture_output=True, text=True)
    r2 = subprocess.run([EXE, str(PATCHER), str(tmp)], capture_output=True, text=True)
    after = target.read_text(encoding="utf-8", errors="surrogateescape")
    check("first run exits 0", r1.returncode == 0, (r1.stdout + r1.stderr).strip())
    check("second run is a no-op",
          r2.returncode == 0 and "already patched" in r2.stdout, r2.stdout.strip())
    check("marker ARCHYTAS_ADRP_RELOC written", "ARCHYTAS_ADRP_RELOC" in after)
    check("guard #ifndef gone", "\n#ifndef CONFIG_ARM64_ERRATUM_843419" not in after)
    check("exactly one #endif removed",
          after.count("#endif") == before.count("#endif") - 1,
          "%d -> %d" % (before.count("#endif"), after.count("#endif")))
    check("ADRP cases now unconditional",
          "case R_AARCH64_ADR_PREL_PG_HI21:" in after
          and "case R_AARCH64_ADR_PREL_PG_HI21_NC:" in after)
    check("braces stay balanced", after.count("{") == after.count("}"))
    check("elsewhere untouched",
          after.count("case R_AARCH64_") == before.count("case R_AARCH64_"))
    bad = subprocess.run([EXE, str(PATCHER), str(tmp / "nope")],
                         capture_output=True, text=True)
    check("bad path exits non-zero", bad.returncode != 0, bad.stderr.strip()[:80])
    shutil.rmtree(tmp, ignore_errors=True)

print("\n== 5. workflow: patch step precedes the kernel build ==")
try:
    import yaml
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["build"]["steps"]
    names = [s.get("name") or ("uses:%s" % s.get("uses")) for s in steps]
    for n in names:
        print("   %2d  %s" % (names.index(n), n))
    def idx(frag):
        return next((i for i, n in enumerate(names) if frag.lower() in n.lower()), None)
    i_patch, i_kern, i_mod = idx("Patch kernel"), idx("Build kernel"), idx("Build connectivity")
    check("patch step exists", i_patch is not None)
    check("kernel build step exists", i_kern is not None)
    check("patch runs BEFORE the kernel build",
          i_patch is not None and i_kern is not None and i_patch < i_kern,
          "patch=%s kernel=%s" % (i_patch, i_kern))
    check("module step runs AFTER the kernel build",
          i_kern is not None and i_mod is not None and i_kern < i_mod,
          "kernel=%s module=%s" % (i_kern, i_mod))
    check("boot-image packaging is gated on the fast lane",
          any("modules_only" in (s.get("if") or "") and "Package boot" in (s.get("name") or "")
              for s in steps))
except ImportError:
    print("   SKIP (pyyaml not installed)")

print("\n== 6. .modinfo extractor on real artifacts ==")
mm = re.search(r"python3 - \"\$m\" <<'PY'\n(.*?)\nPY\n", text, re.S)
kos = sorted(ART.glob("*.ko")) if ART.exists() else []
if not mm or not kos:
    print("   SKIP (extractor not found, or no artifacts at %s)" % ART)
else:
    code = mm.group(1)
    for ko in kos:
        r = subprocess.run([EXE, "-c", code, str(ko)], capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip()
        check("%s readable" % ko.name, r.returncode == 0 and "vermagic=" in out, out)

print("\n================ %s ================" % (
    "ALL CHECKS PASS" if not failures else "FAILURES: " + ", ".join(failures)))
sys.exit(1 if failures else 0)
