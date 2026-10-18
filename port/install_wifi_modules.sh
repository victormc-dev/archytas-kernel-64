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
#
# ★★ 2026-10-18 实测大坑：/vendor (mmcblk0p30) 受 eMMC 写保护，Android 侧写不进去
#   `mount -o rw,remount /vendor` 之后 touch / adb push / sha256sum **全部"成功"**
#   （数据只进了 page cache），真正落盘时 MMC 层报
#       msdc0 XXX CMD<13> resp<0x04000900>, write protection violation
#       mmc_check_write: data error = -30 (EROFS), block addr:0xf0000
#   文件被静默丢弃 ⇒ 重启后原形毕露（mtime/ctime 都回到旧值）。
#   本脚本第 3 步用"写→sync→重挂 ro→还在吗"做探针，命中即中止并提示走 TWRP。
#   **TWRP 里 /vendor 可正常写**：mount -t ext4 -o rw /dev/block/mmcblk0p30 /vendor 后 cp。
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

# ★ eMMC 写保护探针：touch/push 只看 page cache 会被骗，必须做"落盘"判据。
#   写文件 → sync → 重挂 ro（触发 sync_filesystem，写失败会连 inode 一起丢）→ 还在吗？
"$ADB" shell "mount -o rw,remount /vendor" >/dev/null 2>&1
"$ADB" shell "echo wtest > $DEV_MOD/.wtest; sync" >/dev/null 2>&1
"$ADB" shell "mount -o ro,remount /vendor" >/dev/null 2>&1
sleep 1
if ! "$ADB" shell "test -f $DEV_MOD/.wtest"; then
  cat >&2 <<'TWRPMSG'
ERR: /vendor 写入被 eMMC 写保护吞掉 —— 重启后不会生效（详见脚本头 2026-10-18 实测）。
     本机必须改用 TWRP 安装模块：

        adb reboot recovery
        adb wait-for-device
        adb shell "mount -t ext4 -o rw /dev/block/mmcblk0p30 /vendor"
        adb shell "cp <补丁模块.ko> /vendor/lib/modules/wlan_drv_gen4m.ko; sync"
        # 卸载后重挂再比一次 sha256，确认真的落在盘上（不是 page cache）
        adb shell "umount /vendor; mount -t ext4 -o ro /dev/block/mmcblk0p30 /vendor; \
                   sha256sum /vendor/lib/modules/wlan_drv_gen4m.ko; umount /vendor"
        adb reboot
TWRPMSG
  exit 1
fi
"$ADB" shell "rm -f $DEV_MOD/.wtest; mount -o rw,remount /vendor" >/dev/null 2>&1
echo "== /vendor 可写 OK（已过 eMMC 写保护探针）"

# 4) push 四个模块
for m in "${MODULES[@]}"; do
  "$ADB" push "$SRC_DIR/$m" "$DEV_MOD/$m" >/dev/null && echo "   pushed $m"
done
"$ADB" shell "sync; chmod 0644 $DEV_MOD/*.ko; chown root:root $DEV_MOD/*.ko; ls -la $DEV_MOD/"

# ★ push 完必须做"落盘"复核：重挂 ro 再比 sha256。只看 push 回显/page cache 会骗人
#   （2026-10-18：push 报成功、sha256 当场对得上，重启后却是旧文件）。
echo "== 落盘复核（重挂 ro 后读回）..."
"$ADB" shell "mount -o ro,remount /vendor" >/dev/null 2>&1
for m in "${MODULES[@]}"; do
  loc="$(sha256sum "$SRC_DIR/$m" | cut -d' ' -f1)"
  dev="$("$ADB" shell "sha256sum $DEV_MOD/$m 2>/dev/null" | tr -d '\r' | cut -d' ' -f1)"
  if [[ -n "$dev" && "$loc" == "$dev" ]]; then
    echo "   OK   $m  ${loc:0:16}..."
  else
    echo "   FAIL $m  local=${loc:0:16} dev=${dev:0:16}"
    echo "ERR: $m 未真正写入 /vendor（eMMC 写保护）。请走 TWRP，见脚本头。"
    exit 1
  fi
done
"$ADB" shell "mount -o rw,remount /vendor" >/dev/null 2>&1

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
