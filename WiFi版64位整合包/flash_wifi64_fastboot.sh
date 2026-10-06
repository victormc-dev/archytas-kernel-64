#!/usr/bin/env bash
# ============================================================================
# flash_wifi64_fastboot.sh —— 小爱老师 Wi-Fi 版（Archytas）64 位 ROM：fastboot 刷入
#
# 与 flash_wifi64.sh（mtkclient / BROM 下载模式）的区别：
#   * 走 fastboot，不用按键进 BROM、不用 mtkclient、不用管 BROM 授权密码
#   * 进法：adb reboot bootloader（或关机后【音量下 + 电源】）
#
# 前置（已在本机实测核对，脚本会自己再验一遍）：
#   1. bootloader 已解锁（ro.boot.flash.locked=0）
#   2. dm-verity 未启用：/vendor 由 /dev/block/mmcblk0p30 直挂 ext4，
#      不是 /dev/block/dm-X；ro.boot.veritymode 为空
#      => **vbmeta 不需要动**（动它反而有把校验重新打开的风险）
#   3. dtbo：设备上的内容与本包 stock/dtbo.img 逐字节相同
#      => 默认**不刷**（FLASH_DTBO=1 可强制刷）；剩 29 字节是全零填充，刷了也一样
#
# 依赖：fastboot（Android platform-tools）。设 FASTBOOT=/path/to/fastboot.exe 覆盖。
#
# 用法：
#   bash flash_wifi64_fastboot.sh              # 刷 boot+vendor+system，双清
#   NO_WIPE=1 bash flash_wifi64_fastboot.sh    # 不双清（不推荐，换 system 必须清）
#   FLASH_DTBO=1 bash flash_wifi64_fastboot.sh
#   DRY_RUN=1 bash flash_wifi64_fastboot.sh    # 只打印将要执行的命令
#   YES=1 bash flash_wifi64_fastboot.sh        # 跳过二次确认
# ============================================================================
set -uo pipefail

PKG_DIR="$(cd "$(dirname "$0")" && pwd)"
IMGDIR="$PKG_DIR/img"
STOCKDIR="$PKG_DIR/stock"

FB="${FASTBOOT:-}"
if [[ -z "$FB" ]]; then
  for c in fastboot \
           "/d/Downloads/_tools/platform-tools/fastboot.exe" \
           "D:/Downloads/_tools/platform-tools/fastboot.exe"; do
    if command -v "$c" >/dev/null 2>&1 || [[ -x "$c" ]]; then FB="$c"; break; fi
  done
fi
if [[ -z "$FB" ]] || ! { command -v "$FB" >/dev/null 2>&1 || [[ -x "$FB" ]]; }; then
  echo "X 找不到 fastboot。下载 platform-tools 后设 FASTBOOT= 指向它："
  echo "  https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
  exit 1
fi

ADB="${ADB:-}"
if [[ -z "$ADB" ]]; then
  for c in adb "D:/Software/i4Tools9/files/adb/adb.exe"; do
    if command -v "$c" >/dev/null 2>&1 || [[ -x "$c" ]]; then ADB="$c"; break; fi
  done
fi

DRY_RUN="${DRY_RUN:-0}"
YES="${YES:-0}"
NO_WIPE="${NO_WIPE:-0}"
FLASH_DTBO="${FLASH_DTBO:-0}"
FLASH_VBMETA="${FLASH_VBMETA:-0}"

run() {
  if [[ "$DRY_RUN" == "1" ]]; then echo "  [dry-run] $*"; return 0; fi
  echo "  \$ $*"
  "$@"
}

# ---- 预检 ------------------------------------------------------------------
missing=0
for f in "$IMGDIR/boot.img" "$IMGDIR/vendor.img" "$IMGDIR/system.img"; do
  [[ -f "$f" ]] || { echo "X 缺少 $f"; missing=1; }
done
[[ "$missing" == "0" ]] || exit 1

echo "==> fastboot: $FB"
echo "==> adb     : $ADB"
echo ""
echo "================== 将要写入 =================="
printf "  %-8s -> %s\n" boot   "$IMGDIR/boot.img     (64 位内核 4.9.117 + Wi-Fi DTB)"
printf "  %-8s -> %s\n" vendor "$IMGDIR/vendor.img   (原厂 1754 + /lib64 图形栈 + arm64 连通性模块)"
printf "  %-8s -> %s\n" system "$IMGDIR/system.img   (arm64 GSI，1,499,136,000 B)"
[[ "$FLASH_DTBO"   == "1" ]] && printf "  %-8s -> %s\n" dtbo   "$STOCKDIR/dtbo.img  (设备上已与之一致，强制刷)"
[[ "$FLASH_VBMETA" == "1" ]] && printf "  %-8s -> %s\n" vbmeta "$STOCKDIR/vbmeta.img  (强制刷：就地把 disable-verity/verification 位置 1)"
echo ""
echo "  不动：vbmeta / dtbo / lk / lk2 / preloader / recovery / nvram / nvdata / protect* / md1img"
if [[ "$NO_WIPE" == "1" ]]; then
  echo "  双清：**已禁用**（NO_WIPE=1）—— 换 system 后不清数据通常开不了机"
else
  echo "  双清：erase userdata + metadata + cache（fstab 里 userdata/cache 带 formattable，会自动重建）"
fi
echo "============================================="
echo ""

if [[ "$YES" != "1" && "$DRY_RUN" != "1" ]]; then
  read -r -p "确认继续? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "已取消，未做任何写入。"; exit 0; }
fi

# ---- 进 fastboot -----------------------------------------------------------
echo "==> 进入 fastboot（bootloader）..."
if [[ "$DRY_RUN" != "1" ]]; then
  if "$ADB" get-state >/dev/null 2>&1; then
    "$ADB" reboot bootloader
  else
    echo "  !! adb 看不到设备。请手动：关机 -> 按住【音量下 + 电源】进 fastboot"
  fi
  for i in $(seq 1 30); do
    if "$FB" devices 2>/dev/null | grep -q .; then break; fi
    sleep 2
  done
fi
echo "==> fastboot devices:"
run "$FB" devices

if [[ "$DRY_RUN" != "1" ]] && ! "$FB" devices 2>/dev/null | grep -q .; then
  echo "X fastboot 看不到设备。检查："
  echo "  - Windows 设备管理器里是否有 fastboot 接口（MTK preloader/Android ADB Interface）"
  echo "  - 或改用 BROM 路线：bash flash_wifi64.sh"
  exit 1
fi

# ---- 刷入 ------------------------------------------------------------------
echo "==> 刷 boot ..."
run "$FB" flash boot "$IMGDIR/boot.img" || { echo "X boot 失败"; exit 1; }

if [[ "$FLASH_DTBO" == "1" ]]; then
  echo "==> 刷 dtbo ..."
  run "$FB" flash dtbo "$STOCKDIR/dtbo.img" || { echo "X dtbo 失败"; exit 1; }
fi

echo "==> 刷 vendor（400 MiB）..."
run "$FB" flash vendor "$IMGDIR/vendor.img" || { echo "X vendor 失败"; exit 1; }

echo "==> 刷 system（1.4 GiB，最慢的一步；>max-download-size 时 fastboot 会自动分块）..."
run "$FB" flash system "$IMGDIR/system.img" || { echo "X system 失败"; exit 1; }

if [[ "$FLASH_VBMETA" == "1" ]]; then
  echo "==> 刷 vbmeta（强制，关 verity + verification）..."
  run "$FB" --disable-verity --disable-verification flash vbmeta "$STOCKDIR/vbmeta.img"
fi

# ---- 双清 ------------------------------------------------------------------
if [[ "$NO_WIPE" != "1" ]]; then
  echo "==> 双清：userdata / metadata / cache ..."
  run "$FB" erase userdata  || echo "  !! userdata 擦除失败（可试 fastboot -w）"
  run "$FB" erase metadata  || echo "  !! metadata 擦除失败"
  run "$FB" erase cache     || echo "  !! cache 擦除失败"
else
  echo "==> 已跳过双清（NO_WIPE=1）"
fi

echo "==> 重启 ..."
run "$FB" reboot

cat <<'EOT'

✓ 命令执行完毕。首次开机较慢（重建 /data + 首次扫描 arm64 GSI），耐心等 3~8 分钟。

自检清单（开机后）：
  adb shell getprop ro.product.cpu.abi        -> arm64-v8a
  adb shell getprop ro.zygote                 -> zygote64_32
  adb shell getprop ro.product.cpu.abilist    -> arm64-v8a,armeabi-v7a,armeabi
  adb shell ls /vendor/lib64                  -> libusc.so 等 29 个文件
  adb shell ls /system/lib64 | head           -> 应存在
  adb shell getprop ro.build.version.release  -> 9
  adb shell dmesg | grep -i "Find .* in .* BSSes"   # Wi-Fi 侧

回退：
  刷回原厂 boot/dtbo：          fastboot flash boot stock/boot-stock-1754.img
                                fastboot flash dtbo stock/dtbo.img
  刷回原厂 vendor/system：      用 _archytas_rom / 救砖包里的 img/，或设备上刚备份的镜像
  最后：                        fastboot erase userdata; fastboot erase metadata; fastboot reboot
EOT
