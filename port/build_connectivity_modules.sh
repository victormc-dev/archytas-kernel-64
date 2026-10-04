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
#   vendor/mediatek/kernel_modules/connectivity/wlan        -> wlan_6761_axi.ko
#   vendor/mediatek/kernel_modules/connectivity/wlan/adaptor-> wmt_chrdev_wifi.ko
#   vendor/mediatek/kernel_modules/connectivity/bt          -> bt_drv.ko
#
# Usage: bash port/build_connectivity_modules.sh <KERNEL_SRC> <KERNEL_OUT>
#
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# Normalize KROOT/KOUT to ABSOLUTE paths. Kbuild resolves O= relative to the
# directory entered via 'make -C $KROOT', so a relative O= would be re-based as
# $KROOT/$KOUT (e.g. kernel/kernel/out-archytas) and the prebuilt .config there
# would be missing -> "Configuration file .config not found" at modules_prepare.
abspath() { ( cd "$(dirname "$1")" 2>/dev/null && echo "$(pwd)/$(basename "$1")" ) || echo "$1"; }
KROOT="$(abspath "${1:-$ROOT/kernel}")"
KOUT="$(abspath "${2:-$ROOT/kernel/out-archytas}")"
CROSS_COMPILE="${CROSS_COMPILE:-aarch64-linux-gnu-}"
JOBS="${JOBS:-$(nproc 2>/dev/null || echo 4)}"

CONN="vendor/mediatek/kernel_modules/connectivity"
AUTOCONF="$KOUT/include/generated/autoconf.h"

echo "== build_connectivity_modules =="
echo "KROOT = $KROOT"
echo "KOUT  = $KOUT"
echo "CROSS = $CROSS_COMPILE"
test -f "$AUTOCONF" || { echo "ERROR: $AUTOCONF missing; build the kernel (modules_prepare) first"; exit 1; }
command -v git  >/dev/null 2>&1 || { echo "ERROR: git required"; exit 1; }
command -v patch >/dev/null 2>&1 || echo "WARN: patch not found; relying on git apply only"

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
      local api_sha
      api_sha="$(curl -fsSL "$api" 2>/dev/null \
                 | sed -n 's/^ *"sha": *"\([0-9a-f]\{40\}\)".*/\1/p' | head -1)"
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
  make -C "$KROOT" O="$KOUT" ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" \
       HOSTCFLAGS="-fcommon -Wno-error" HOSTCXXFLAGS="-fcommon -Wno-error" \
       KCFLAGS="-Wno-error -include $WLAN_DIR/emi_compat.h" \
       AUTOCONF_H="$AUTOCONF" KERNEL_OUT="$KOUT" TOP="$KROOT" \
       KBUILD_MODPOST_FAIL_ON_WARNINGS= \
       M="$CONN/$d" "$@" modules -j"$JOBS"
}

build_mod common  CONFIG_MTK_COMBO_CHIP=CONSYS_6761
build_mod wlan/adaptor CONFIG_MTK_COMBO_CHIP=CONSYS_6761
build_mod bt      CONFIG_MTK_COMBO_CHIP=CONSYS_6761
build_mod wlan    MTK_COMBO_CHIP=MT6761 \
                    CONFIG_MTK_COMBO_WIFI_HIF=axi \
                    CONFIG_MTK_COMBO_WIFI=m \
                    CONFIG_WLAN_DRV_BUILD_IN=n

# ---- 5. Collect the four modules -------------------------------------------
OUTDIR="$KOUT/connectivity-modules"
mkdir -p "$OUTDIR"
find "$KOUT" "$KROOT/$CONN" -name 'wmt_drv.ko'        -exec cp -f {} "$OUTDIR/" \;
find "$KOUT" "$KROOT/$CONN" -name 'wmt_chrdev_wifi.ko' -exec cp -f {} "$OUTDIR/" \;
find "$KOUT" "$KROOT/$CONN" -name 'bt_drv.ko'         -exec cp -f {} "$OUTDIR/" \;
_w=$(find "$KOUT" "$KROOT/$CONN" -name 'wlan_6761_axi.ko' | head -1)
if [ -n "$_w" ]; then cp -f "$_w" "$OUTDIR/wlan_drv_gen4m.ko"; fi

echo "=== built connectivity modules ==="
ls -l "$OUTDIR"/*.ko 2>/dev/null || { echo "ERROR: no modules produced"; exit 1; }

# vermagic sanity check
echo "=== vermagic ==="
for m in "$OUTDIR"/*.ko; do
  echo "$(basename "$m"): $(modinfo -k "$(uname -r)" "$m" 2>/dev/null | grep -i vermagic || true)"
done
