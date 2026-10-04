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
  6. module build order + immediate collection. build_mod() wipes the module's
     whole build directory, and 'wlan' is an ancestor of 'wlan/adaptor' -- so
     building the ancestor last DELETES an already-linked module, and a
     collect-at-the-end sweep then cannot find it. That was run #37189514054:
     wmt_chrdev_wifi.ko linked fine at 08:47:34, gone by the 08:48:14 find.
  7. the `.modinfo` extractor reads name=/vermagic= out of a real .ko.
  8. the CONFIG_MTK_COMBO_CHIP quoting fix + the two IC-ops gates. Kconfig keeps
     its string values quoted and common/Makefile matches them with quoted
     patterns (`$(filter "CONSYS_%",...)`), so overriding the variable with a
     BARE `CONSYS_6761` on the make command line silently switched off
     common_main/platform/mt6761.o -- the module then kept the __weak
     mtk_wcn_get_consys_ic_ops stub and mtk_wmt_probe() panicked on a NULL
     wmt_consys_ic_ops. Includes a negative control on the pre-fix artifact.

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

# Optional: point the shell-script checks at a different copy, e.g. an older
# revision, to prove they actually fail on the bug they are meant to catch:
#   git show HEAD:port/build_connectivity_modules.sh > /tmp/old.sh
#   python3 port/verify_build_script.py /tmp/old.sh
if len(sys.argv) > 1:
    SCRIPT = Path(sys.argv[1])

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

print("\n== 6. module build order + immediate collection ==")
# Logical lines: drop comments, join backslash continuations. The `build_mod
# wlan` invocation spans 5 physical lines, so physical-line scanning is wrong.
_logical = []
_buf = ""
for _ln in text.splitlines():
    _s = _ln.split("#", 1)[0] if _ln.lstrip().startswith("#") else _ln
    if not _buf and not _s.strip():
        continue
    if _buf and _s.lstrip().startswith("#"):
        continue
    _buf += _s
    if _buf.rstrip().endswith("\\"):
        _buf = _buf.rstrip()[:-1]
        continue
    _logical.append(_buf.strip())
    _buf = ""

_builds = [(i, l.split()[1]) for i, l in enumerate(_logical) if l.startswith("build_mod ")]
_collects = [(i, l.split()[1]) for i, l in enumerate(_logical) if l.startswith("collect_mod ")]
_order = [d for _, d in _builds]
print("   build_mod order : %s" % " -> ".join(_order))
print("   collect_mod     : %s" % " -> ".join(d for _, d in _collects))

check("all four modules are built",
      sorted(_order) == sorted(["common", "wlan", "wlan/adaptor", "bt"]),
      "got %s" % _order)

# (a) every build must be immediately followed by its own collect_mod, before
#     the next build starts. Otherwise a later ancestor wipe can still lose it.
_missing_collect = []
for k, (bi, bd) in enumerate(_builds):
    nxt = _builds[k + 1][0] if k + 1 < len(_builds) else len(_logical)
    own = [ci for ci, cd in _collects if cd == bd and bi < ci < nxt]
    if not own:
        _missing_collect.append(bd)
check("each module is collected right after it is built", not _missing_collect,
      "no collect_mod before the next build_mod for: %s" % _missing_collect)

# (b) an ancestor directory must be built before its descendants, because
#     build_mod() wipes the whole directory. 'wlan' must precede 'wlan/adaptor'.
_bad_pairs = []
for i, a in enumerate(_order):
    for j, b in enumerate(_order):
        if a != b and b.startswith(a + "/") and j < i:
            _bad_pairs.append("%s built after %s" % (a, b))
check("ancestor modules are built before their descendants", not _bad_pairs,
      "; ".join(_bad_pairs))

# (c) OUTDIR must be wiped before the first build, or a stale .ko from an
#     earlier local run would satisfy the "all four present" assertion.
_outdir_def = next((i for i, l in enumerate(_logical)
                    if l.startswith('OUTDIR="$KOUT/connectivity-modules"')), None)
_wipe = next((i for i, l in enumerate(_logical) if l.startswith('rm -rf "$OUTDIR"')), None)
_first_build = _builds[0][0] if _builds else None
check("OUTDIR is defined and cleaned before the first build",
      None not in (_outdir_def, _wipe, _first_build)
      and _outdir_def < _wipe < _first_build,
      "define=%s wipe=%s first build=%s" % (_outdir_def, _wipe, _first_build))

# (d) the artifact assertion must name all four, and the rename step must not be
#     able to mistake a previously renamed file for the freshly built module.
_want = re.search(r"for _want in ([^;]+); do", text)
check("artifact assertion covers all four modules",
      bool(_want) and set(_want.group(1).split()) >=
      {"wmt_drv", "wmt_chrdev_wifi", "bt_drv", "wlan_drv_gen4m"},
      _want.group(1).strip() if _want else "not found")
check("rename step skips an already-renamed wlan_drv_gen4m.ko",
      "case \"$_cand\" in *'/wlan_drv_gen4m.ko') continue" in text)

# (e) the pre-rename copy must be deleted, or the artifact glob ships the same
#     ~55 MiB wlan core twice.
_i_rmdup = next((i for i, l in enumerate(_logical) if l.startswith('rm -f "$_w"')), None)
check("pre-rename wlan core copy is removed (no 55 MiB duplicate)", _i_rmdup is not None)

# (f) ordering: relocation profile (which needs .debug_info for DW_AT_producer)
#     -> strip -g -> .modinfo dump. Profiling after the strip would lose the
#     codegen flags; verifying modinfo before the strip would not prove the
#     SHIPPED bytes are intact.
_i_prof = next((i for i, l in enumerate(_logical)
                if l.startswith('echo "=== relocation profile ==="')), None)
_i_strip = next((i for i, l in enumerate(_logical) if '"$STRIP" -g "$_m"' in l), None)
_i_modi = next((i for i, l in enumerate(_logical)
                if l.startswith('echo "=== modinfo')), None)
check("relocation profile is read before the strip", None not in (_i_prof, _i_strip)
      and _i_prof < _i_strip, "profile=%s strip=%s" % (_i_prof, _i_strip))
check(".modinfo is re-read AFTER the strip (verifies the shipped bytes)",
      None not in (_i_strip, _i_modi) and _i_strip < _i_modi,
      "strip=%s modinfo=%s" % (_i_strip, _i_modi))

print("\n== 7. .modinfo extractor on real artifacts ==")
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

print("\n== 8. COMBO_CHIP quoting fix + IC-ops linkage gates ==")
# (a) The regression itself. Kconfig stores string symbols WITH quotes
#     (auto.conf: CONFIG_MTK_COMBO_CHIP="CONSYS_6761") and common/Makefile:198
#     matches them with a quoted pattern, so passing a bare word on the make
#     command line makes every one of those filters miss. "$COMBO_CHIP_MAKE" is
#     the good form; a literal bare value is the bug.
_bare = [m for m in re.findall(r'CONFIG_MTK_COMBO_CHIP=(?!")([^\s\\"\']+)', text)
         if not m.startswith("$")]
check("CONFIG_MTK_COMBO_CHIP is never passed as a bare word", not _bare,
      "bare value(s): %s" % _bare)
check("the value is re-quoted before it is handed to make",
      'COMBO_CHIP_MAKE="\\"$COMBO_CHIP\\""' in text)
check("combo chip comes from the kernel's own config, not a literal",
      "kcfg_value() {" in text and "include/config/auto.conf" in text)
check("MTK_PLATFORM is derived with a non-empty fallback",
      'MTK_PLATFORM_VALUE=$(unq "$(kcfg_value CONFIG_MTK_PLATFORM' in text
      and "MTK_PLATFORM_VALUE=mt6761" in text)
check("MTK_PLATFORM is passed to the common module build",
      bool(re.search(r"build_mod common[\s\S]{0,160}?MTK_PLATFORM=\"\$MTK_PLATFORM_VALUE\"",
                     text)))

# (b) Gate #1 (chip file compiled) must sit between build_mod common and the
#     next module, so its failure is attributed to common's own build.
_i_bc = next((li for li, d in _builds if d == "common"), None)
_i_bw = next((li for li, d in _builds if d == "wlan"), None)
_i_g1 = next((i for i, l in enumerate(_logical)
              if "WMT IC-ops provider is in the link" in l), None)
check("mt6761.o presence gate runs after build_mod common and before wlan",
      None not in (_i_bc, _i_g1, _i_bw) and _i_bc < _i_g1 < _i_bw,
      "common@%s gate@%s wlan@%s" % (_i_bc, _i_g1, _i_bw))

# (c) Gate #2 must inspect the SHIPPED bytes, i.e. run after `strip -g`, and must
#     go through the symbol table (string counting cannot distinguish a
#     surviving __weak body from an overridden one).
_i_g2 = next((i for i, l in enumerate(_logical)
              if "IC-ops linkage gate" in l), None)
check("IC-ops symbol gate runs after the strip (gates the shipped .ko)",
      None not in (_i_g2, _i_strip) and _i_strip < _i_g2,
      "strip@%s gate@%s" % (_i_strip, _i_g2))
check("IC-ops gate goes through port/elf_symbols.py",
      _i_g2 is not None
      and "elf_symbols.py" in "\n".join(_logical[_i_g2 + 1: _i_g2 + 3]),
      "next lines: %s" % (_logical[_i_g2 + 1: _i_g2 + 3] if _i_g2 is not None else None))

# (d) The two regexes the gate relies on, tested against real symbol lines.
_rx = re.findall(r"grep -qE '(\^  (?:mtk_wcn_get_consys_ic_ops|consys_ic_ops)[^']+)'", text)
check("both gate regexes are present", len(_rx) == 2, "found %d" % len(_rx))
if len(_rx) == 2:
    rx_icops, rx_table = re.compile(_rx[0]), re.compile(_rx[1])
    STRONG = ("  mtk_wcn_get_consys_ic_ops                      "
              "GLOBAL FUNC    1     0x3f20       24")
    WEAKLINE = ("  mtk_wcn_get_consys_ic_ops                      "
                "WEAK   FUNC    1     0x71a0       68")
    TABLE = ("  consys_ic_ops                                  "
             "GLOBAL OBJECT  3     0x110        172")
    check("gate matches a STRONG provider", bool(rx_icops.match(STRONG)), STRONG.strip())
    check("gate rejects the __weak stub", not rx_icops.match(WEAKLINE), WEAKLINE.strip())
    check("gate matches the IC ops table", bool(rx_table.match(TABLE)), TABLE.strip())

# (e) Functional test of the symbol parser on a real module, plus a negative
#     control on the pre-fix artifact when it is still on disk.
SYMBOLS = ROOT / "port" / "elf_symbols.py"
STOCK = ROOT / "port" / "_device_backup" / "modules_stock" / "wmt_drv.ko"
if not SYMBOLS.exists() or not STOCK.exists():
    print("   SKIP (need port/elf_symbols.py and the stock vendor module)")
else:
    r = subprocess.run([EXE, str(SYMBOLS), str(STOCK),
                        "--grep", r"^mtk_wcn_get_consys_ic_ops$|^consys_ic_ops$"],
                       capture_output=True, text=True)
    check("elf_symbols.py reads ELF32 and finds the stock strong provider",
          r.returncode == 0 and "GLOBAL FUNC" in r.stdout
          and re.search(r"^  consys_ic_ops\s+GLOBAL OBJECT", r.stdout, re.M) is not None,
          (r.stdout + r.stderr).strip().replace("\n", " | ")[:160])
    PRE = (ROOT / "_ci" / "37190391324" / "kernel" / "out-archytas"
           / "connectivity-modules" / "wmt_drv.ko")
    if not PRE.exists():
        print("   SKIP negative control (pre-fix artifact not downloaded)")
    else:
        r = subprocess.run([EXE, str(SYMBOLS), str(PRE),
                            "--grep", r"^mtk_wcn_get_consys_ic_ops$|^consys_ic_ops$"],
                           capture_output=True, text=True)
        out = r.stdout
        check("negative control: the pre-fix build shows WEAK + no IC ops table",
              "WEAK" in out and re.search(r"^  consys_ic_ops\s", out, re.M) is None,
              out.strip().replace("\n", " | ")[:160])

print("\n================ %s ================" % (
    "ALL CHECKS PASS" if not failures else "FAILURES: " + ", ".join(failures)))
sys.exit(1 if failures else 0)
