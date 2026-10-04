#!/usr/bin/env bash
# ============================================================================
# install_wifi_modules.sh —— 把 64 位连通性模块装到已启动的 Archytas 设备
#
# 背景：原厂 /vendor/lib/modules/*.ko 是 32 位 ARMv7（vermagic ARMv7），
# 在 64 位 aarch64 内核里 insmod 必失败 → vendor.connsys.driver.ready 永不置位
# → wmt_drv/wmt_chrdev_wifi/wlan_drv_gen4m/bt_drv 全部不加载 → 无 wlan0 / 无蓝牙。
#
# 原厂 init 的加载顺序（/vendor/etc/init/*.rc）：
#   on boot                                   -> insmod wmt_drv.ko
#   on property:vendor.connsys.driver.ready=yes -> insmod wmt_chrdev_wifi.ko
#                                                 insmod wlan_drv_${ro.vendor.wlan.gen}.ko
#                                                 insmod bt_drv.ko
#
# 用法：
#   bash port/install_wifi_modules.sh [模块目录]
#     模块目录默认 port/../archytas-wifi-boot-and-modules/kernel/out-archytas/connectivity-modules
#     也可直接指向解压出来的 CI artifact 目录。
#
# 做了什么：
#   1) 备份设备原 /vendor/lib/modules 到本地 port/_device_backup/modules_stock
#   2) adb root + 重新挂载 /vendor 为可写
#   3) push 四个 64 位 .ko 覆盖同名文件（wlan_*_axi.ko 已由 CI 改名为 wlan_drv_gen4m.ko）
#   4) 按原厂顺序 insmod 并验证（lsmod / wlan0 / dmesg）
#
# 依赖：adb（默认取 D:/Downloads/platform-tools/adb.exe，可用 ADB 覆盖）
# ============================================================================
set -uo pipefail

ADB="${ADB:-/d/Downloads/platform-tools/adb.exe}"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="${1:-$ROOT_DIR/archytas-wifi-boot-and-modules/kernel/out-archytas/connectivity-modules}"
BAK_DIR="$ROOT_DIR/port/_device_backup/modules_stock"
DEV_MOD="/vendor/lib/modules"

# 模块名 -> 设备上的目标文件名（build 脚本已把 wlan_*_axi.ko 重命名为 wlan_drv_gen4m.ko）
MODULES=(wmt_drv.ko wmt_chrdev_wifi.ko wlan_drv_gen4m.ko bt_drv.ko)
# 原厂 init 的加载顺序
LOAD_ORDER=(wmt_drv.ko wmt_chrdev_wifi.ko wlan_drv_gen4m.ko bt_drv.ko)

echo "== install_wifi_modules =="
echo "ADB     = $ADB"
echo "SRC_DIR = $SRC_DIR"
[[ -x "$ADB" ]] || { echo "ERR: 找不到 adb: $ADB"; exit 1; }

# 0) 设备在线检查
"$ADB" wait-for-device
echo "== 设备: $("$ADB" shell getprop ro.product.device) / $("$ADB" shell getprop ro.build.version.release)"
echo "== 内核: $("$ADB" shell cat /proc/version)"

# 1) 备份原厂模块（只做一次，存在即跳过）
mkdir -p "$BAK_DIR"
for m in "${MODULES[@]}"; do
  if [[ ! -f "$BAK_DIR/$m" ]]; then
    "$ADB" pull "$DEV_MOD/$m" "$BAK_DIR/$m" >/dev/null 2>&1 && echo "   backup: $m"
  fi
done
echo "== 原厂模块备份在 $BAK_DIR"

# 2) 校验待装机模块（存在 + aarch64 vermagic）
for m in "${MODULES[@]}"; do
  [[ -f "$SRC_DIR/$m" ]] || { echo "ERR: 缺模块 $SRC_DIR/$m"; exit 1; }
  vm="$(grep -a -o -E '4\.9\.117[+ ]+SMP[^ ]* [^ ]* [^ ]* [^ ]* [^ ]* [^ ]*' "$SRC_DIR/$m" 2>/dev/null | head -1)"
  echo "   $m: $(stat -c%s "$SRC_DIR/$m") bytes  vermagic=[${vm:-?}]"
done

# 3) root + 挂载 /vendor 可写
"$ADB" root >/dev/null 2>&1 || true
sleep 1
"$ADB" wait-for-device
"$ADB" remount >/dev/null 2>&1 || true
# remount 失败就手动重挂
if ! "$ADB" shell "touch $DEV_MOD/.wtest 2>/dev/null && rm -f $DEV_MOD/.wtest"; then
  echo "== 手动重挂 /vendor rw ..."
  "$ADB" shell "mount -o remount,rw /vendor"
fi
"$ADB" shell "touch $DEV_MOD/.wtest && rm -f $DEV_MOD/.wtest" \
  || { echo "ERR: /vendor 仍不可写（AVB/verity？）"; exit 1; }
echo "== /vendor 可写 OK"

# 4) push 四个模块
for m in "${MODULES[@]}"; do
  "$ADB" push "$SRC_DIR/$m" "$DEV_MOD/$m" >/dev/null && echo "   pushed $m"
done
"$ADB" shell "sync; chmod 0644 $DEV_MOD/*.ko; chown root:root $DEV_MOD/*.ko; ls -la $DEV_MOD/"

# 5) 按原厂顺序 insmod（先 rmmod 旧的，忽略错误）
echo "== 按原厂顺序加载..."
for m in "${LOAD_ORDER[@]}"; do
  mod="${m%.ko}"
  "$ADB" shell "rmmod $mod 2>/dev/null; insmod $DEV_MOD/$m && echo '   loaded '$m || echo '   FAILED  '$m"
done

# 6) 触发 vendor 属性（若 init 未自动置位，供 wpa_supplicant 使用）
"$ADB" shell "setprop vendor.connsys.driver.ready yes" 2>/dev/null || true

# 7) 验证
echo "== lsmod =="
"$ADB" shell "lsmod"
echo "== ip link =="
"$ADB" shell "ip link show wlan0 2>/dev/null || echo 'no wlan0'"
echo "== dmesg(wmt/wlan) 末尾 =="
"$ADB" shell "dmesg | grep -iE 'wmt|wlan|gen4m|connsys' | tail -25"
echo "== 完成。若仍无 wlan0，看上面 FAILED 行与 dmesg 报错。"
