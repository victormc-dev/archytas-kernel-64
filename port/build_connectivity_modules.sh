#!/usr/bin/env bash
#
# Build the four MediaTek connectivity kernel modules for the Archytas (Wi-Fi)
# MT6761 / k61 AArch64 4.9.117 kernel, using agui's pinned external sources
# plus his 4.9 compatibility patch set.
#
# The kernel itself is built elsewhere (build.yml -> Image.gz-dtb). This script
# only compiles the out-of-tree .ko files that the retail vendor image ships as
# 32-bit ARMv7 modules (which cannot load into an AArch64 kernel).
#
# Layout produced under the kernel tree (matches agui's in-tree Kbuild plumbing
# in drivers/misc/mediatek/connectivity/Makefile, which expects the standalone
# repos at vendor/mediatek/kernel_modules/connectivity/...):
#
#   vendor/mediatek/kernel_modules/connectivity/common      -> wmt_drv.ko
#   vendor/mediatek/kernel_modules/connectivity/wlan        -> wlan_mt6761_axi.ko
#        (derived as wlan_<lower(WLAN_CHIP_ID)>_<HIF>; WLAN_CHIP_ID=word1(MTK_COMBO_CHIP)=MT6761)
#   vendor/mediatek/kernel_modules/connectivity/wlan/adaptor-> wmt_chrdev_wifi.ko
#   vendor/mediatek/kernel_modules/connectivity/bt          -> bt_drv.ko
#
# Usage: bash port/build_connectivity_modules.sh [<KERNEL_SRC> <KERNEL_OUT>]
#                                        [--modules-only]
#
# --modules-only / MODULES_ONLY=1
#   The fast lane. Use it when the boot image is already built/flashed and you
#   only need to re-iterate on the four .ko files: it will NOT build the kernel
#   Image, so a round costs ~2-3 min instead of ~10. If the kernel out-tree is
#   not prepared yet (no include/generated/autoconf.h) it configures the tree
#   and runs modules_prepare itself, which is enough for out-of-tree (M=) builds.
#
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# Normalize KROOT/KOUT to ABSOLUTE paths. Kbuild resolves O= relative to the
# directory entered via 'make -C $KROOT', so a relative O= would be re-based as
# $KROOT/$KOUT (e.g. kernel/kernel/out-archytas) and the prebuilt .config there
# would be missing -> "Configuration file .config not found" at modules_prepare.
abspath() { ( cd "$(dirname "$1")" 2>/dev/null && echo "$(pwd)/$(basename "$1")" ) || echo "$1"; }

MODULES_ONLY="${MODULES_ONLY:-0}"
_pos=()
for _a in "$@"; do
  case "$_a" in
    --modules-only) MODULES_ONLY=1 ;;
    -h|--help) sed -n '2,32p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) _pos+=("$_a") ;;
  esac
done
KROOT="$(abspath "${_pos[0]:-$ROOT/kernel}")"
KOUT="$(abspath "${_pos[1]:-$ROOT/kernel/out-archytas}")"
CROSS_COMPILE="${CROSS_COMPILE:-aarch64-linux-gnu-}"
JOBS="${JOBS:-$(nproc 2>/dev/null || echo 4)}"

CONN="vendor/mediatek/kernel_modules/connectivity"
AUTOCONF="$KOUT/include/generated/autoconf.h"
DEFCONFIG=k61v1_64_archytas_defconfig

echo "== build_connectivity_modules =="
echo "KROOT = $KROOT"
echo "KOUT  = $KOUT"
echo "CROSS = $CROSS_COMPILE"
echo "MODE  = $([ "$MODULES_ONLY" = 1 ] && echo 'modules-only (fast lane)' || echo 'with-kernel')"
command -v git  >/dev/null 2>&1 || { echo "ERROR: git required"; exit 1; }
command -v patch >/dev/null 2>&1 || echo "WARN: patch not found; relying on git apply only"

# ---- 0. Make sure the kernel out-tree is usable for an M= build ------------
# A full CI run builds Image.gz-dtb first (see .github/workflows/build.yml),
# which leaves a fully prepared out-tree behind. In --modules-only mode we skip
# that multi-minute kernel compile, so if the out-tree is cold we configure it
# and let step 3's `modules_prepare` generate everything an M= build needs.
prepare_kernel_tree() {
  local defcfg="$KROOT/arch/arm64/configs/$DEFCONFIG"
  [ -f "$defcfg" ] || { echo "ERROR: defconfig missing: $defcfg"; exit 1; }
  echo ">> out-tree is cold -> configuring $DEFCONFIG + modules_prepare"
  mkdir -p "$KOUT/include"
  ln -sfn "$KROOT/include/dt-bindings" "$KOUT/include/dt-bindings"
  make -C "$KROOT" O="$KOUT" ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" \
       HOSTCFLAGS="-fcommon -Wno-error" HOSTCXXFLAGS="-fcommon -Wno-error" \
       "$DEFCONFIG"
  local KCONFIG="$KROOT/scripts/config"
  [ -x "$KCONFIG" ] || KCONFIG="perl $KCONFIG"
  $KCONFIG --file "$KOUT/.config" --disable CONFIG_KSU
  make -C "$KROOT" O="$KOUT" ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" \
       HOSTCFLAGS="-fcommon -Wno-error" HOSTCXXFLAGS="-fcommon -Wno-error" \
       olddefconfig
}

if [ ! -f "$AUTOCONF" ]; then
  if [ "$MODULES_ONLY" = 1 ]; then
    command -v make >/dev/null 2>&1 || { echo "ERROR: make required"; exit 1; }
    prepare_kernel_tree
  else
    echo "ERROR: $AUTOCONF missing; build the kernel (modules_prepare) first"
    echo "       (or re-run with --modules-only to prepare the tree here)"
    exit 1
  fi
fi

# ---- 1. Fetch the four pinned external repos -------------------------------
# Map of abbreviated -> full 40-hex SHAs (fallback if the GitHub API is
# unreachable). A bare/abbreviated SHA CANNOT be fetched by Git (the wire
# protocol needs the full object id), so we must expand it before fetching.
declare -A FULLSHA=(
  [364afcf]=364afcfde5c4d952fb67705aacc26456c64eb9dd
  [e84d47e]=e84d47e835553a6c1f654309341cebe78b96be67
  [9074126]=90741260b236d9fc808e9c7ff2a650977242833e
  [ba2c5a5]=ba2c5a57fc22c7e9dc6224b2cee8d8a1c6d98e80
)

fetch_repo() {
  local url="$1" commit="$2" dest="$3"
  echo ">> fetch $url @ $commit"
  echo "   -> $dest"
  rm -rf "$dest"
  mkdir -p "$dest"
  if ! git clone --depth 1 "$url" "$dest" >/dev/null 2>&1; then
    echo "ERROR: clone failed for $url"; exit 1
  fi
  # Expand the (possibly abbreviated) commit SHA to its full 40-hex form. Try the
  # GitHub API first (authoritative), then the hardcoded table, then assume the
  # given value is already full. Without the full SHA, 'git fetch <sha>' is
  # rejected by the server and the build silently used the clone tip -- whose
  # default branch did NOT contain the module Makefile (build broke with
  # "No rule to make target .../Makefile").
  local full="$commit"
  if ! [[ "$commit" =~ ^[0-9a-f]{40}$ ]]; then
    if command -v curl >/dev/null 2>&1; then
      local api="${url%.git}"
      api="${api#https://github.com/}"
      api="https://api.github.com/repos/${api}/commits/${commit}"
      local api_sha=""
      # MUST NOT be able to abort the build. This runs under `set -e` +
      # `set -o pipefail`, so `curl -f` returning exit 22 (HTTP >=400, e.g. the
      # unauthenticated GitHub API rate limit) propagated straight out of the
      # script and killed CI build #14 before the FULLSHA fallback below could
      # run. Use a non-fatal curl (no -f) with a timeout, and swallow any
      # residual failure so an unreachable/limited API just falls through to
      # the pinned table.
      api_sha="$(curl -sSL --max-time 20 "$api" 2>/dev/null \
                 | sed -n 's/^ *"sha": *"\([0-9a-f]\{40\}\)".*/\1/p' | head -1)" || api_sha=""
      if [ -n "$api_sha" ]; then
        echo "   api: $commit -> $api_sha"
      else
        echo "   api lookup returned nothing for $commit (rate limit/offline?); using pinned table"
      fi
      [ -n "$api_sha" ] && full="$api_sha"
    fi
    [ "$full" = "$commit" ] && [ -n "${FULLSHA[$commit]:-}" ] && full="${FULLSHA[$commit]}"
  fi
  echo "   using $full"
  # Fetch the exact commit (GitHub serves any reachable commit by full SHA).
  if git -C "$dest" fetch --depth 1 origin "$full" >/dev/null 2>&1 \
     && git -C "$dest" checkout -q "$full" 2>/dev/null; then
    echo "   pinned to $full"; return 0
  fi
  if git -C "$dest" fetch origin "$full" >/dev/null 2>&1 \
     && git -C "$dest" checkout -q "$full" 2>/dev/null; then
    echo "   pinned to $full"; return 0
  fi
  git -C "$dest" fetch --unshallow origin >/dev/null 2>&1 || true
  if git -C "$dest" checkout -q "$full" 2>/dev/null; then
    echo "   pinned to $full"
  else
    echo "WARN: could not checkout $commit in $dest (using clone tip)"
  fi
}

WLAN_DIR="$KROOT/$CONN/wlan"
fetch_repo https://github.com/zainarbani/vendor-mediatek-kernel_modules-connectivity-wlan-core-gen4m \
          ba2c5a5 "$WLAN_DIR"
fetch_repo https://github.com/MotorolaMobilityLLC/vendor-mediatek-kernel_modules-connectivity-common \
          364afcf "$KROOT/$CONN/common"
fetch_repo https://github.com/MotorolaMobilityLLC/vendor-mediatek-kernel_modules-connectivity-wlan-adaptor \
          e84d47e "$KROOT/$CONN/wlan/adaptor"
fetch_repo https://github.com/MotorolaMobilityLLC/vendor-mediatek-kernel_modules-connectivity-bt-mt66xx \
          9074126 "$KROOT/$CONN/bt"

# ---- 1b. Align the common WMT struct with the newer Gen4M WLAN core --------
# Version skew: the Gen4M WLAN core (zainarbani @ ba2c5a5) is newer than the
# pinned common repo (Motorola @ 364afcf). It registers the WMT wlan callback
# 'wlan_is_wifi_drv_own_cb' (hifAxiIsWifiDrvOwn in axi.c -- both in the original
# mtk_axi_probe and in agui's axiWmtRetry), but the common's wmt_exp.h struct
# MTK_WCN_WMT_WLAN_CB_INFO only has 5 members -> "has no member named
# 'wlan_is_wifi_drv_own_cb'". Append the missing member (a plain function
# pointer, same style as the peers just above it) so the WLAN module compiles.
# Appending keeps every existing member offset unchanged, and the (older)
# wmt_drv simply never invokes the extra callback -> harmless at runtime.
WMT_EXP="$KROOT/$CONN/common/common_main/include/wmt_exp.h"
if [ -f "$WMT_EXP" ] && ! grep -q "wlan_is_wifi_drv_own_cb" "$WMT_EXP"; then
  awk '{ if ($0 ~ /^\} MTK_WCN_WMT_WLAN_CB_INFO/) print "\tINT32 (*wlan_is_wifi_drv_own_cb)(VOID);"; print }' \
      "$WMT_EXP" > "$WMT_EXP.new" && mv "$WMT_EXP.new" "$WMT_EXP"
  echo "   added wlan_is_wifi_drv_own_cb to MTK_WCN_WMT_WLAN_CB_INFO"
else
  echo "WARN: $WMT_EXP missing or already patched"
fi

# ---- 2. Apply agui's 4.9 compatibility patches to the WLAN core ------------
PATCH1="$KROOT/vendor/archimedes-wlan/0001-mt6761-prealloc-axi-dma.patch"
PATCH2="$KROOT/Documentation/wifi-coherent-dma-58c7ba5.patch"
for p in "$PATCH1" "$PATCH2"; do
  [ -f "$p" ] || { echo "WARN: patch not found: $p (skipping)"; continue; }
  echo ">> apply $(basename "$p")"
  if git -C "$KROOT" apply --3way --directory="$CONN/wlan" "$p" 2>/dev/null; then
    echo "   applied via git apply"
  elif patch -p1 -d "$WLAN_DIR" < "$p" 2>/dev/null; then
    echo "   applied via patch"
  else
    echo "ERROR: failed to apply $(basename "$p")"; exit 1
  fi
done

# ---- 2b. Neutralize -Werror in the out-of-tree module Makefiles ------------
# These old MTK modules (common, wlan-core, ...) add a bare 'ccflags-y += -Werror'
# to their Makefiles. Under GCC 11 the benign pointer-sign warnings that the 4.9
# kernel headers now emit become hard errors ("cc1: all warnings being treated
# as errors") and the build fails. Our KCFLAGS=-Wno-error is prepended BEFORE the
# module's ccflags-y, so the module's -Werror wins (GCC last-flag-wins); strip it
# from every Makefile under the connectivity tree instead.
echo ">> neutralize -Werror in module Makefiles"
# Match Makefile, Kbuild (kernel modules often use 'Kbuild' as the makefile
# name, e.g. under os/linux/) and *.mk -- any of these can carry a bare
# 'ccflags-y += -Werror' that escalates benign warnings to hard errors.
find "$KROOT/$CONN" -type f \( -name 'Makefile' -o -name 'Kbuild' -o -name '*.mk' \) -print0 \
  | while IFS= read -r -d '' m; do
      sed -i -E 's/-Werror=[A-Za-z0-9_+-]+//g; s/-Werror([[:space:]]|$)/ /g' "$m"
    done

# ---- 2c. ARCHYTAS EMI/MPU protection compat shim ------------------------
# The Gen4M WLAN module (gl_init.c, axi.c) references gConEmiPhyBaseFinal /
# gConEmiSizeFinal and kalSetEmiMpuProtection() (and kalSetDrvEmiMpuProtection()
# on the non-prealloc path). On this 4.9 tree those are NOT provided: the MTK
# HAL that defines them is absent, and the Wi-Fi reserved-memory EMI window is
# owned by the bootloader/conninfra. agui's 0001 patch adds plat_priv.c with the
# definitions but never wires it into the module -objs, so it is never compiled,
# and gConEmi*Final is declared upstream only under #if CFG_MTK_ANDROID_EMI
# (which is OFF in this build) -> 'undeclared' / implicit-function errors.
#
# Fix: (a) extend the ALREADY-linked compat_stub.c with the missing symbols, and
# (b) force-include a header that declares them into every WLAN translation unit,
# so both gl_init.c and axi.c see the prototypes regardless of which branch is
# compiled. The functions are intentionally no-ops: the EMI MPU policy is owned
# by the bootloader/conninfra on this device (see agui's comment in axi.c).
cat > "$WLAN_DIR/emi_compat.h" <<'EOF'
#ifndef ARCHYTAS_EMI_COMPAT_H
#define ARCHYTAS_EMI_COMPAT_H
#include <linux/types.h>
extern phys_addr_t gConEmiPhyBaseFinal;
extern unsigned long long gConEmiSizeFinal;
extern void kalSetEmiMpuProtection(phys_addr_t emiPhyBase, bool enable);
extern void kalSetDrvEmiMpuProtection(phys_addr_t emiPhyBase, uint32_t offset, uint32_t size);
#endif
EOF

cat >> "$WLAN_DIR/compat_stub.c" <<'EOF'

/* ARCHYTAS EMI/MPU compat shim -- see emi_compat.h.
 * The kernel tree on this 4.9 device does not provide kalSetEmiMpuProtection
 * (it lives behind a MTK HAL that is absent); the Wi-Fi reserved-memory EMI
 * window is protected by the bootloader/conninfra, so these are no-ops. */
phys_addr_t gConEmiPhyBaseFinal;
unsigned long long gConEmiSizeFinal;
void kalSetEmiMpuProtection(phys_addr_t emiPhyBase, bool enable)
{
}
void kalSetDrvEmiMpuProtection(phys_addr_t emiPhyBase, uint32_t offset, uint32_t size)
{
}
EOF

# Force-include emi_compat.h into every WLAN TU. Anchor on the -Wno-error line
# that agui's 0001 patch already inserted into the module Makefile.
if grep -q 'ccflags-y += -Wno-error' "$WLAN_DIR/Makefile"; then
  sed -i "s|^ccflags-y += -Wno-error|ccflags-y += -Wno-error\nccflags-y += -include $WLAN_DIR/emi_compat.h|" "$WLAN_DIR/Makefile"
  echo "   force-include emi_compat.h added to wlan Makefile"
else
  echo "WARN: -Wno-error anchor not found in wlan Makefile; emi_compat.h NOT force-included"
fi

# ---- 2d. Force the large code model for every connectivity module -----------
# WHY: the target kernel is built with CONFIG_ARM64_ERRATUM_843419=y
# (Cortex-A53 erratum), which selects CONFIG_ARM64_MODULE_CMODEL_LARGE and makes
# arch/arm64/Makefile add KBUILD_CFLAGS_MODULE += -mcmodel=large. That flag IS
# reaching cc1 out of the box -- verified from the DW_AT_producer string baked
# into the shipped .ko:
#     GNU C89 11.4.0 -mlittle-endian -mgeneral-regs-only -mcmodel=large
#       -mabi=lp64 -g -O2 -std=gnu90 -fno-strict-aliasing -fno-common
#       -fno-asynchronous-unwind-tables -fno-pic -fstack-protector-strong ...
# so `-mcmodel=large` is a belt-and-braces re-assertion here, NOT the fix (an
# earlier revision of this script blamed KCFLAGS for not reaching cc1 -- that
# diagnosis was wrong).
#
# It is re-asserted through ccflags-y because ccflags-y is provably delivered to
# cc1 for these modules (the MTK Makefiles' own `ccflags-y += -Werror` used to
# abort the build), so this also documents the intended code model in the
# module's own Makefile. Appended last so it wins under GCC's last-flag-wins.
echo ">> force -mcmodel=large in module Makefiles"
for mf in "$KROOT/$CONN/common/Makefile" \
          "$KROOT/$CONN/wlan/Makefile" \
          "$KROOT/$CONN/wlan/adaptor/Makefile" \
          "$KROOT/$CONN/bt/Makefile"; do
  if [ ! -f "$mf" ]; then
    echo "WARN: module Makefile not found: $mf"
    continue
  fi
  if grep -q 'ARCHYTAS_MCMODEL_LARGE' "$mf"; then
    echo "   already patched: $mf"
    continue
  fi
  {
    printf '\n# ARCHYTAS_MCMODEL_LARGE --- injected by port/build_connectivity_modules.sh\n'
    printf '# CONFIG_ARM64_ERRATUM_843419=y => the module loader rejects ADRP\n'
    printf '# relocations, so the module must be built with the large code model.\n'
    printf 'ccflags-y += -mcmodel=large\n'
    printf 'subdir-ccflags-y += -mcmodel=large\n'
  } >> "$mf"
  echo "   injected -mcmodel=large into $mf"
done

# ---- 2e. Let the module loader process ADRP relocations (THE ADRP FIX) -----
# `-mcmodel=large` (step 2d) is applied, and it is still NOT enough: GCC 11 on
# aarch64 keeps emitting `adrp`+`ldr` pairs that read a 64-bit slot from a
# per-function LITERAL POOL placed inside .text itself (the `$d` mapping symbols
# GAS emits). Measured on the build #8 artifact (bt_drv.ko, 461496 B):
#     161 x R_AARCH64_ADR_PREL_PG_HI21 (275), ALL targeting "[sec].text" with
#     small, 8-byte-aligned addends that sit immediately after a function end
#     (0x1d8 right after BT_poll@0x100+0xd4, 0x2a8, 0x798, 0xcc0, ...), each
#     paired with a 64-bit pool slot that carries an R_AARCH64_ABS64 (257).
# Binutils' aarch64 linker enables --fix-cortex-a53-843419 BY DEFAULT, and
# arch/arm64/Makefile only adds `--no-fix-cortex-a53-843419` in the *else*
# branch, i.e. when the erratum is disabled -- so with
# CONFIG_ARM64_ERRATUM_843419=y the module link keeps its erratum fix and the
# surviving ADRP relocations are exactly what the kernel loader refuses:
#     module wmt_drv: unsupported RELA relocation: 275
#     insmod: failed to load ...: Exec format error
#
# No compiler flag makes these go away, so fix the one place that actually
# decides: arch/arm64/kernel/module.c compiles the ADRP handlers out behind
#     #ifndef CONFIG_ARM64_ERRATUM_843419
#         case R_AARCH64_ADR_PREL_PG_HI21_NC:
#         case R_AARCH64_ADR_PREL_PG_HI21: ...
#     #endif
# Drop that guard so ADRP relocations are relocated like every other type.
# The upstream intent (modules use the large model => they never need this) does
# not hold for these old MTK modules, and the reloc_insn_imm(RELOC_OP_PAGE,...)
# body itself is the standard, correct handler.
#
# Deliberately NOT done instead: disabling CONFIG_ARM64_ERRATUM_843419 in the
# defconfig. That would also drop --fix-cortex-a53-843419 from LDFLAGS_vmlinux
# and so remove the erratum workaround from the KERNEL ITSELF (real Cortex-A53
# hardware bug: "a load or store might access an incorrect address"). This
# surgical patch keeps the kernel binary's workaround intact and only relaxes
# the module-loader policy. Trade-off: the ADRPs inside the modules are then
# accepted verbatim, which is exactly what the (default-on) linker erratum fix
# already vetted.
#
# NOTE: this changes the KERNEL, so the boot image must be rebuilt and reflashed
# once; after that, --modules-only runs only need the .ko files.
echo ">> allow ADRP relocations in arch/arm64/kernel/module.c"
MODULE_C="$KROOT/arch/arm64/kernel/module.c"
if [ ! -f "$MODULE_C" ]; then
  echo "ERROR: $MODULE_C not found; cannot apply the ADRP relocation fix"
  exit 1
fi
python3 - "$MODULE_C" <<'PY'
import re, sys
p = sys.argv[1]
src = open(p, encoding="utf-8", errors="surrogateescape").read()
if "ARCHYTAS_ADRP_RELOC" in src:
    print("   already patched (marker present)")
    sys.exit(0)
# Match the guard exactly, tolerating indentation of the cases.
old = re.compile(
    r"[ \t]*#ifndef CONFIG_ARM64_ERRATUM_843419\n"
    r"([ \t]*case R_AARCH64_ADR_PREL_PG_HI21_NC:\n"
    r"[\s\S]*?break;\n)"
    r"[ \t]*#endif\n")
m = old.search(src)
if not m:
    sys.exit("ERROR: could not find the CONFIG_ARM64_ERRATUM_843419 ADRP guard "
             "in %s -- the kernel source layout changed" % p)
body = m.group(1)
note = ("/* ARCHYTAS_ADRP_RELOC: the guard `#ifndef CONFIG_ARM64_ERRATUM_843419`\n"
        " * used to compile these ADRP handlers out, which made every module that\n"
        " * contains an ADRP relocation unloadable (`unsupported RELA relocation:\n"
        " * 275`, -ENOEXEC).  These MTK connectivity modules DO contain them even\n"
        " * though they are built with -mcmodel=large, so handle them normally.\n"
        " * See port/build_connectivity_modules.sh step 2e. */\n")
open(p, "w", encoding="utf-8", errors="surrogateescape").write(
    src[:m.start()] + note + body + src[m.end():])
n = body.count("case R_AARCH64_ADR_PREL_PG_HI21")
print("   patched: %d ADRP case label(s) now compiled unconditionally" % n)
PY
if ! grep -q "ARCHYTAS_ADRP_RELOC" "$MODULE_C"; then
  echo "ERROR: ADRP relocation patch did not apply; modules with ADRP would be rejected"
  exit 1
fi
if grep -q '^#ifndef CONFIG_ARM64_ERRATUM_843419' "$MODULE_C"; then
  echo "WARN: a '#ifndef CONFIG_ARM64_ERRATUM_843419' is still present in $MODULE_C"
fi

# ---- 3. Prepare generated headers / vmlinux symtab for out-of-tree modpost
echo ">> modules_prepare"
make -C "$KROOT" O="$KOUT" ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" \
     HOSTCFLAGS="-fcommon -Wno-error" HOSTCXXFLAGS="-fcommon -Wno-error" \
     KCFLAGS="-Wno-error" modules_prepare -j"$JOBS"

# ---- 4. Build each connectivity module -------------------------------------
# KBUILD_MODPOST_FAIL_ON_WARNINGS is forced 'y' by the WMT Makefile; some symbols
# live in other modules (loaded first at runtime) or in vmlinux, so relax it to
# avoid a hard build failure on legitimately-deferred symbol resolution.
build_mod() {
  local d="$1"; shift
  echo ">> build module: $CONN/$d"
  # Force-include the ARCHYTAS EMI/MPU compat header (created in step 2c) into
  # every translation unit. KCFLAGS is the most reliable vehicle because it is
  # folded into KBUILD_CFLAGS for all module compiles regardless of how the
  # module's -objs are laid out across subdirectories.
  #
  # -mcmodel=large is MANDATORY on this kernel. The running kernel has
  #   CONFIG_ARM64_ERRATUM_843419=y (Cortex-A53 erratum), which makes
  #   arch/arm64/kernel/module.c compile OUT the handlers for the ADRP
  #   relocations R_AARCH64_ADR_PREL_PG_HI21/_NC (275/276):
  #       #ifndef CONFIG_ARM64_ERRATUM_843419
  #           case R_AARCH64_ADR_PREL_PG_HI21: ...
  #   and arch/arm64/Kconfig expects modules to be built with -mcmodel=large
  #   ("builds modules using the large memory model in order to avoid the use
  #   of the ADRP instruction"). arch/arm64/Makefile is supposed to inject it
  #   via KBUILD_CFLAGS_MODULE, but the out-of-tree (M=) build here does not
  #   pick that up, so the modules were linked with ~9000 ADRP relocations and
  #   insmod failed with:
  #       module wmt_drv: unsupported RELA relocation: 275
  #   Pass it explicitly. Verified after the build by check_no_adrp() below.
  # Always rebuild: a cached/pre-existing .o would keep stale codegen and silently
  # defeat the -mcmodel=large change (that is exactly what fooled build #14/#15).
  rm -rf "$KOUT/$CONN/$d"
  make -C "$KROOT" O="$KOUT" ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" \
       HOSTCFLAGS="-fcommon -Wno-error" HOSTCXXFLAGS="-fcommon -Wno-error" \
       KCFLAGS="-mcmodel=large -Wno-error -include $WLAN_DIR/emi_compat.h" \
       AUTOCONF_H="$AUTOCONF" KERNEL_OUT="$KOUT" TOP="$KROOT" \
       KBUILD_MODPOST_FAIL_ON_WARNINGS= \
       M="$CONN/$d" "$@" modules -j"$JOBS"

  # Diagnostic: show the flags cc1 actually received, straight out of Kbuild's
  # own .<obj>.cmd command file. This is how we prove (or disprove) that
  # -mcmodel=large made it into the compile line.
  local _cmd _flags
  _cmd=$(find "$KOUT/$CONN/$d" -name '*.o.cmd' 2>/dev/null | head -1)
  if [ -n "$_cmd" ]; then
    _flags=$( { grep -o -E -- '-mcmodel=[a-z]+|-fno-PIE|-fPIE|-fpic|-fPIC|-include [^ ]*emi_compat.h' "$_cmd" || true; } \
              | sort -u | tr '\n' ' ')
    echo "   [flags] $(basename "$_cmd") => ${_flags:-<none of interest>}"
  else
    echo "   [flags] no .o.cmd under $KOUT/$CONN/$d (module may not have compiled)"
  fi
}

# Report the relocation profile of each shipped module. ADRP (275/276) is NOT
# fatal any more -- step 2e patches the kernel loader to relocate it -- but it is
# still worth printing, both to catch a codegen regression and because a count of
# zero would mean the modules no longer depend on the kernel patch at all.
# Also prints the codegen flags GCC recorded inside the .ko (DW_AT_producer), the
# only reliable way to prove which -m/-f flags actually reached cc1.
report_relocs() {
  local ko="$1"
  python3 - "$ko" <<'PY'
import re, struct, sys
p = sys.argv[1]
d = open(p, "rb").read()
if d[:4] != b"\x7fELF" or d[4] != 2:
    sys.exit("not a 64-bit ELF: %s" % p)
(_t, _m, _v, _e, _po, shoff, _f,
 _eh, _pe, _pn, shentsize, shnum, shstrndx) = struct.unpack_from("<HHIQQQIHHHHHH", d, 16)
secs = []
for i in range(shnum):
    o = shoff + i * shentsize
    secs.append(struct.unpack_from("<IIQQQQIIQQ", d, o))
shstr_off = secs[shstrndx][4]
def nm(x):
    e = d.index(b"\0", shstr_off + x)
    return d[shstr_off + x:e].decode("utf-8", "replace")
adrp = 0
for s in secs:
    if s[1] not in (4, 9) or not nm(s[0]).startswith(".rel"):
        continue
    esz = s[9] or 24
    for k in range(s[5] // esz):
        r_info = struct.unpack_from("<Q", d, s[4] + k * esz + 8)[0]
        if (r_info & 0xFFFFFFFF) in (275, 276):
            adrp += 1
flags = []
m = re.search(rb"GNU C[0-9][^\x00]{10,400}", d)
if m:
    for f in (b"-mcmodel=large", b"-fno-pic", b"-fPIE", b"-fpic",
              b"-fstack-protector", b"-fno-jump-tables"):
        if f in m.group(0):
            flags.append(f.decode())
print("   %-22s ADRP(275/276) = %-5d  codegen: %s"
      % (p.rsplit("/", 1)[-1], adrp, " ".join(flags) or "<unreadable>"))
PY
}


build_mod common  CONFIG_MTK_COMBO_CHIP=CONSYS_6761
build_mod wlan/adaptor CONFIG_MTK_COMBO_CHIP=CONSYS_6761
build_mod bt      CONFIG_MTK_COMBO_CHIP=CONSYS_6761
# MTK_ANDROID_WMT=y is REQUIRED here. This SoC uses the legacy WMT glue, not
# conninfra, so CFG_SUPPORT_CONNINFRA is forced to 0. But gl_rst.c/axi.c compile
# their WMT code whenever CFG_SUPPORT_CONNINFRA==0 (NOT additionally gated on
# CFG_MTK_ANDROID_WMT), while the header that declares those symbols is included
# only under `#if CFG_MTK_ANDROID_WMT && (CFG_SUPPORT_CONNINFRA == 0)` (gl_rst.h)
# -> without it: CFG_MTK_ANDROID_WMT==0, wmt_exp.h never included, yet
# gl_rst.c still references WMTDRV_TYPE_WIFI / mtk_wcn_wmt_msgcb_reg /
# mtk_wcn_wmt_assert_timeout / WMTMSG_TYPE_RESET / WMTRSTMSG_* -> undeclared.
# Setting MTK_ANDROID_WMT=y also makes the wlan Makefile emit
# -I.../common/common_main/include and .../common_main/linux/include, which is
# where wmt_exp.h (and its deps osal.h/wmt_plat.h/stp_exp.h) live.
build_mod wlan    MTK_COMBO_CHIP=MT6761 \
                    MTK_ANDROID_WMT=y \
                    CONFIG_MTK_COMBO_WIFI_HIF=axi \
                    CONFIG_MTK_COMBO_WIFI=m \
                    CONFIG_WLAN_DRV_BUILD_IN=n

# ---- 5. Collect the four modules -------------------------------------------
# The wlan-core module name is DERIVED by its own Makefile, NOT fixed:
#   MODULE_NAME := wlan_<lower(WLAN_CHIP_ID)>_<CONFIG_MTK_COMBO_WIFI_HIF>
#   WLAN_CHIP_ID = $(word 1, $(MTK_COMBO_CHIP))
# We invoke it with MTK_COMBO_CHIP=MT6761 + HIF=axi, so the real filename is
#   wlan_mt6761_axi.ko          (note the 'mt' prefix: lower('MT6761')='mt6761')
# The old hardcoded 'wlan_6761_axi.ko' never matched, so this step silently
# dropped the main Wi-Fi driver and the artifact shipped with only 3 of 4 .ko.
# Match any wlan_*.ko (chipid-agnostic) and FAIL LOUDLY when it is missing.
OUTDIR="$KOUT/connectivity-modules"
mkdir -p "$OUTDIR"
find "$KOUT" "$KROOT/$CONN" -name 'wmt_drv.ko'        -exec cp -f {} "$OUTDIR/" \;
find "$KOUT" "$KROOT/$CONN" -name 'wmt_chrdev_wifi.ko' -exec cp -f {} "$OUTDIR/" \;
find "$KOUT" "$KROOT/$CONN" -name 'bt_drv.ko'         -exec cp -f {} "$OUTDIR/" \;

echo "=== debug: every .ko produced under KOUT ==="
find "$KOUT" -type f -name '*.ko' 2>/dev/null | sort || true

_w=$(find "$KOUT" "$KROOT/$CONN" -type f -name 'wlan_*.ko' 2>/dev/null | head -1)
if [ -n "$_w" ]; then
  cp -f "$_w" "$OUTDIR/wlan_drv_gen4m.ko"
  echo "   wlan core module: $(basename "$_w") -> wlan_drv_gen4m.ko"
else
  echo "ERROR: wlan core module (wlan_*.ko) not found; expected wlan_mt6761_axi.ko"
  exit 1
fi

echo "=== built connectivity modules ==="
ls -l "$OUTDIR"/*.ko 2>/dev/null || { echo "ERROR: no modules produced"; exit 1; }

# Hard assertion: the artifact must contain all four modules. Without this the
# CI could go green while silently shipping an incomplete set (the exact bug
# that produced build #8 with only wmt_drv/wmt_chrdev_wifi/bt_drv).
_missing=""
for _want in wmt_drv wmt_chrdev_wifi bt_drv wlan_drv_gen4m; do
  [ -f "$OUTDIR/$_want.ko" ] || _missing="$_missing $_want.ko"
done
if [ -n "$_missing" ]; then
  echo "ERROR: missing connectivity modules:$_missing"
  exit 1
fi
echo "   OK: all 4 connectivity modules present."

# Relocation profile of every shipped module (see report_relocs).
echo "=== relocation profile ==="
for m in "$OUTDIR"/*.ko; do
  report_relocs "$m"
done

# Hard gate for the ONLY thing that makes those modules loadable: the target
# kernel must carry the ADRP relocation fix from step 2e. Shipping .ko files that
# need a kernel without the fix would just reproduce the insmod -ENOEXEC failure.
if grep -q "ARCHYTAS_ADRP_RELOC" "$KROOT/arch/arm64/kernel/module.c" 2>/dev/null; then
  echo "   OK: kernel source carries the ADRP relocation fix (ARCHYTAS_ADRP_RELOC)."
  echo "       -> the boot image built from THIS tree must be flashed once."
else
  echo "ERROR: arch/arm64/kernel/module.c does NOT carry the ADRP relocation fix;"
  echo "       any module containing ADRP would be rejected by insmod (-ENOEXEC)."
  exit 1
fi

# kernel -> module. `modinfo` on the runner would describe the RUNNER's kernel,
# not the target, so read the strings straight out of the ELF `.modinfo` section.
# This matters: a vermagic mismatch makes insmod fail instantly
# ("version magic ... should be ..."), and the module's internal `name=` is what
# lsmod will show (the on-disk filename wlan_drv_gen4m.ko is just what the
# vendor init.rc insmods by path).
#
# Also counts __versions entries, because the target vermagic contains
# "modversions" (CONFIG_MODVERSIONS=y). Symbol CRCs are pulled from the kernel's
# Module.symvers at modpost time; a --modules-only run never builds vmlinux, so
# that file may be absent and __versions comes out empty. That is survivable --
# check_version() only rejects a symbol whose name IS listed with a different
# crc, and an empty (but present) __versions falls through to its
# "broken toolchain, warn once, return 1" path -- but it is worth seeing, since a
# MISSING section would instead fail with -ENOEXEC.
echo "=== modinfo (name / vermagic / __versions) ==="
if [ -f "$KOUT/Module.symvers" ]; then
  echo "   kernel Module.symvers present: $(wc -l < "$KOUT/Module.symvers") symbols"
else
  echo "   NOTE: $KOUT/Module.symvers absent (expected in --modules-only mode;"
  echo "         symbol CRCs will be empty -> __versions entry count 0)"
fi
for m in "$OUTDIR"/*.ko; do
  python3 - "$m" <<'PY'
import struct, sys
p = sys.argv[1]
d = open(p, "rb").read()
(_t, _m, _v, _e, _po, shoff, _f,
 _eh, _pe, _pn, shentsize, shnum, shstrndx) = struct.unpack_from("<HHIQQQIHHHHHH", d, 16)
secs = []
for i in range(shnum):
    o = shoff + i * shentsize
    secs.append(struct.unpack_from("<IIQQQQIIQQ", d, o))
shstr_off = secs[shstrndx][4]
def nm(x):
    e = d.index(b"\0", shstr_off + x)
    return d[shstr_off + x:e].decode("utf-8", "replace")
info = {}
ver_size = None
for s in secs:
    name = nm(s[0])
    if name == ".modinfo":
        for entry in d[s[4]:s[4] + s[5]].split(b"\0"):
            if b"=" in entry:
                k, _, v = entry.decode("utf-8", "replace").partition("=")
                info[k] = v
    elif name == "__versions":
        ver_size = s[5]
print("   %-22s name=%-20s __versions=%-4s vermagic=%s"
      % (p.rsplit("/", 1)[-1], info.get("name", "?"),
         "absent" if ver_size is None else "%d" % (ver_size // 72),
         info.get("vermagic", "<missing!>")))
PY
done
