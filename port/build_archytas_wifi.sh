#!/usr/bin/env bash
# ============================================================================
# build_archytas_wifi.sh  --  Build the MT6761 kernel for the Wi-Fi board and
# package a 32 MiB MTK boot image using the Wi-Fi board DTB.
#
# Prerequisites (run on a Linux build host, NOT macOS):
#   - aarch64-linux-gnu-gcc / binutils (cross toolchain)
#   - make, python3, dtc is NOT required (DTB comes from the Wi-Fi ROM)
#   - the archimedes-kernel-64-main tree (this script expects it as a sibling
#     of the port/ directory, or set KERNEL=)
#
# What it does:
#   1. Copies k61v1_64_archytas_defconfig into the kernel tree.
#   2. Builds arch/arm64/boot/Image.gz-dtb (kernel code == 4G build).
#   3. Swaps the 4G DTB inside the 32 MiB template for the Wi-Fi DTB.
#   4. Packages boot-archytas-wifi.img with the validated MTK v1 layout.
#
# Output:
#   port/boot-archytas-wifi.img  (exactly 32 MiB, flash to the boot partition)
# ============================================================================
set -euo pipefail

PORT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
KERNEL=${KERNEL:-"$PORT/../archimedes-kernel-64-main"}
TEMPLATE=${TEMPLATE:-"$KERNEL/tools/archimedes-boot-template-32MiB.img"}
WIFI_DTB=${WIFI_DTB:-"$PORT/wifi_stock.dtb"}
CROSS=${CROSS_COMPILE:-aarch64-linux-gnu-}
JOBS=${JOBS:-$(nproc 2>/dev/null || echo 24)}
OUT=${OUT:-"$PORT/out-archytas-wifi"}
WIFI_TEMPLATE=${WIFI_TEMPLATE:-"$PORT/wifi-template-32MiB.img"}
BOOT_OUT=${BOOT_OUT:-"$PORT/boot-archytas-wifi.img"}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
command -v make >/dev/null 2>&1 || die 'make is required'
command -v "${CROSS}gcc" >/dev/null 2>&1 || die "missing ${CROSS}gcc cross toolchain"
command -v python3 >/dev/null 2>&1 || die 'python3 is required'
[[ -f "$TEMPLATE" ]] || die "template not found: $TEMPLATE"
[[ -f "$WIFI_DTB" ]] || die "wifi dtb not found: $WIFI_DTB"
[[ -d "$KERNEL" ]] || die "kernel tree not found: $KERNEL"

# 1) install defconfig into the kernel tree
cp "$PORT/k61v1_64_archytas_defconfig" "$KERNEL/arch/arm64/configs/k61v1_64_archytas_defconfig"

# 2) build kernel (out-of-tree)
echo "==> building kernel (defconfig=k61v1_64_archytas_defconfig, jobs=$JOBS)"
make -C "$KERNEL" O="$OUT" ARCH=arm64 CROSS_COMPILE="$CROSS" k61v1_64_archytas_defconfig
# ---- optional Wi-Fi-only modem disable: uncomment to enable ----
# "$KERNEL/scripts/config" --file "$OUT/.config" --disable CONFIG_MTK_CCCI_DEVICES
# "$KERNEL/scripts/config" --file "$OUT/.config" --disable CONFIG_MTK_NET_CCMNI
# "$KERNEL/scripts/config" --file "$OUT/.config" --disable CONFIG_MTK_ECCCI_DRIVER
# "$KERNEL/scripts/config" --file "$OUT/.config" --disable CONFIG_MTK_ECCCI_CLDMA
# "$KERNEL/scripts/config" --file "$OUT/.config" --disable CONFIG_MTK_ECCCI_CCIF
# "$KERNEL/scripts/config" --file "$OUT/.config" --disable CONFIG_MTK_ECCCI_C2K
# "$KERNEL/scripts/config" --file "$OUT/.config" --disable CONFIG_MTK_CONN_MD
# make -C "$KERNEL" O="$OUT" ARCH=arm64 CROSS_COMPILE="$CROSS" olddefconfig
make -C "$KERNEL" O="$OUT" ARCH=arm64 CROSS_COMPILE="$CROSS" -j"$JOBS" Image.gz-dtb

IMG="$OUT/arch/arm64/boot/Image.gz-dtb"
[[ -f "$IMG" ]] || die "kernel build did not produce $IMG"

# 3) make Wi-Fi boot template (swap 4G DTB -> Wi-Fi DTB)
echo "==> making Wi-Fi boot template"
python3 "$PORT/make_wifi_template.py" --template "$TEMPLATE" --wifi-dtb "$WIFI_DTB" --output "$WIFI_TEMPLATE"

# 4) package
echo "==> packaging boot image"
python3 "$KERNEL/package_archimedes_boot.py" --template "$WIFI_TEMPLATE" --kernel "$IMG" --output "$BOOT_OUT"

echo "==> DONE: $BOOT_OUT"
ls -l "$BOOT_OUT"
