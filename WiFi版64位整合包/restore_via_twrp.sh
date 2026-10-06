#!/usr/bin/env bash
# ============================================================================
# restore_via_twrp.sh —— 用 TWRP（recovery）把设备刷回「WiFi 版 64 位 ROM 整合包」
#
# 场景：设备已经能进 TWRP（`adb devices` 里显示为 recovery），但 system 被刷坏、
#       或想从别的 ROM 回退。**不需要 mtkclient、不需要 fastboot、不需要 BROM。**
#
# 原理：TWRP 的 adbd 是 root（u:r:su:s0），可以直接 dd 写裸分区：
#         boot   -> /dev/block/by-name/boot   (mmcblk0p25)
#         vendor -> /dev/block/by-name/vendor (mmcblk0p30)
#         system -> /dev/block/by-name/system (mmcblk0p31)
#       镜像先 push 到已被格式化的 /data（12 GiB，放得下 1.9 GiB），
#       逐个 sha256 校验通过后才 dd；写完再从分区读回比对，最后重置 userdata。
#
# 用法：
#   bash restore_via_twrp.sh                  # 刷 boot+vendor+system，并重置 userdata
#   SKIP_WIPE=1 bash restore_via_twrp.sh      # 不重置 userdata（换 system 后通常开不了机）
#   NO_VERIFY=1 bash restore_via_twrp.sh      # 跳过 sha256（快，不推荐）
#   NO_MOUNT=1 bash restore_via_twrp.sh       # /data 已挂载时用
#   DEVICE=<serial> ADB=/path/to/adb bash restore_via_twrp.sh
#
# 前置：设备已经停在 TWRP 主界面；USB 已连；`adb devices` 能看到 recovery。
# ============================================================================
set -uo pipefail

PKG_DIR="$(cd "$(dirname "$0")" && pwd)"
IMGDIR="$PKG_DIR/img"

# MSYS/Git-Bash：adb 是原生程序，禁掉路径转换；此时源路径须写 Windows 风格 D:/...
export MSYS_NO_PATHCONV=1
winpath() { printf '%s' "$1" | sed -e 's|^/\([a-zA-Z]\)/|\1:/|'; }

ADB="${ADB:-}"
if [[ -z "$ADB" ]]; then
  for c in adb "D:/Software/i4Tools9/files/adb/adb.exe"; do
    if command -v "$c" >/dev/null 2>&1 || [[ -x "$c" ]]; then ADB="$c"; break; fi
  done
fi
[[ -n "$ADB" ]] || { echo "X 找不到 adb。设 ADB=/path/to/adb"; exit 1; }

DEVICE="${DEVICE:-}"
adb_() {
  if [[ -n "$DEVICE" ]]; then "$ADB" -s "$DEVICE" "$@"; else "$ADB" "$@"; fi
}

SKIP_WIPE="${SKIP_WIPE:-0}"
NO_VERIFY="${NO_VERIFY:-0}"
NO_MOUNT="${NO_MOUNT:-0}"
STAGE="${STAGE:-/mnt/data}"          # 镜像落脚点（在 data 上）

# ---- 预检 ------------------------------------------------------------------
missing=0
for f in "$IMGDIR/boot.img" "$IMGDIR/vendor.img" "$IMGDIR/system.img"; do
  [[ -f "$f" ]] || { echo "X 缺少 $f"; missing=1; }
done
[[ "$missing" == "0" ]] || exit 1

echo "==> adb: $ADB"
echo ""
echo "================== 设备状态 =================="
adb_ get-state 2>&1 || true
SERIAL="$(adb_ devices | awk 'NR>1 && $2=="recovery"{print $1; exit}')"
if [[ -z "$SERIAL" && -z "$DEVICE" ]]; then
  echo "X adb 里没有 recovery 状态的设备。"
  echo "  1) 确认设备停在 TWRP 主界面（不是黑屏、不是正常系统）"
  echo "  2) 或有多台设备时用 DEVICE=<serial> 指定"
  adb_ devices
  exit 1
fi
[[ -n "$DEVICE" ]] || DEVICE="$SERIAL"
echo "==> 目标设备: $DEVICE"
echo ""

echo "================== 将要写入 =================="
printf "  %-8s -> %-34s %s\n" boot   "/dev/block/by-name/boot"   "$(stat -c%s "$IMGDIR/boot.img") B"
printf "  %-8s -> %-34s %s\n" vendor "/dev/block/by-name/vendor" "$(stat -c%s "$IMGDIR/vendor.img") B"
printf "  %-8s -> %-34s %s\n" system "/dev/block/by-name/system" "$(stat -c%s "$IMGDIR/system.img") B"
if [[ "$SKIP_WIPE" == "1" ]]; then
  echo "  userdata：**不重置**（SKIP_WIPE=1）—— 换 system 后通常开不了机"
else
  echo "  userdata：重新格式化为 ext4（清数据）"
fi
echo "  不动：vbmeta / dtbo / lk / lk2 / preloader / md1img / nvram / nvdata / recovery"
echo "============================================="
echo ""
read -r -p "确认继续? [y/N] " ans
[[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "已取消，未做任何写入。"; exit 0; }

# ---- 分区容量核对 ----------------------------------------------------------
echo "==> 核对分区容量（防写超界）..."
for pair in "boot:boot.img" "vendor:vendor.img" "system:system.img"; do
  part="${pair%%:*}"; img="${pair##*:}"
  dev="/dev/block/by-name/$part"
  psize="$(adb_ shell "blockdev --getsize64 $dev 2>/dev/null || cat /sys/class/block/\$(basename \$(readlink -f $dev))/size" | tr -d '\r\n')"
  isize="$(stat -c%s "$IMGDIR/$img")"
  if [[ -n "$psize" && "$psize" -gt 0 ]]; then
    # 若拿到的是 512B 扇区数（sysfs size），换算成字节
    [[ "$psize" -lt "$isize" ]] && psize=$((psize * 512))
  fi
  printf "  %-7s part=%-12s image=%-12s %s\n" "$part" "${psize:-?}" "$isize" \
         "$( { [[ -n "$psize" ]] && (( psize >= isize )); } && echo OK || echo '!! 容量未知或不足' )"
done
echo ""

# ---- 准备落脚点 ------------------------------------------------------------
if [[ "$NO_MOUNT" != "1" ]]; then
  echo "==> 格式化并挂载 /data 作为落脚点 ..."
  adb_ shell "umount $STAGE 2>/dev/null; mke2fs -F -t ext4 -b 4096 /dev/block/by-name/userdata >/dev/null 2>&1; mkdir -p $STAGE; mount -t ext4 -o rw /dev/block/by-name/userdata $STAGE" || {
    echo "X 挂载 $STAGE 失败。可手动挂载后加 NO_MOUNT=1 重跑。"; exit 1; }
fi
adb_ shell "df -h $STAGE" 2>&1

# ---- 推送 + 校验 -----------------------------------------------------------
declare -A LOCAL_SHA
push_and_check() {
  local name="$1" img="$2"
  local lp; lp="$(winpath "$IMGDIR/$img")"
  echo ""
  echo "==> push $img ($(stat -c%s "$IMGDIR/$img") B) -> $STAGE/$img"
  adb_ push "$lp" "$STAGE/$img" 2>&1 | tail -1
  LOCAL_SHA["$img"]="$(sha256sum "$IMGDIR/$img" | cut -d' ' -f1)"
  echo "    local  sha256 = ${LOCAL_SHA[$img]}"
  if [[ "$NO_VERIFY" != "1" ]]; then
    local rsha; rsha="$(adb_ shell "sha256sum $STAGE/$img" | tr -d '\r' | cut -d' ' -f1)"
    echo "    device sha256 = $rsha"
    if [[ "$rsha" != "${LOCAL_SHA[$img]}" ]]; then
      echo "X $img 传输后哈希不一致，已中止（设备上文件可能损坏，请重试）"; exit 1
    fi
    echo "    -> 一致 OK"
  fi
}
push_and_check boot   boot.img
push_and_check vendor vendor.img
push_and_check system system.img

# ---- 写入 ------------------------------------------------------------------
echo ""
echo "==> dd 写入分区 ..."
for pair in "boot:boot.img" "vendor:vendor.img" "system:system.img"; do
  part="${pair%%:*}"; img="${pair##*:}"
  echo "  $img -> /dev/block/by-name/$part"
  # 注意：TWRP 的 dd 是 toybox，**不接受 bs=4M 这类后缀**（报 illegal number），
  # 必须写纯字节数。bs=4194304 或 1048576 均可。
  adb_ shell "dd if=$STAGE/$img of=/dev/block/by-name/$part bs=4194304 2>&1 | tail -1; sync" || {
    echo "X $part 写入失败"; exit 1; }
done

# ---- 读回比对 --------------------------------------------------------------
if [[ "$NO_VERIFY" != "1" ]]; then
  echo ""
  echo "==> 从分区读回比对（这才是真正的落地证据）..."
  for pair in "boot:boot.img" "vendor:vendor.img" "system:system.img"; do
    part="${pair%%:*}"; img="${pair##*:}"
    bytes="$(stat -c%s "$IMGDIR/$img")"
    blks=$(( bytes / 4096 ))
    rsha="$(adb_ shell "dd if=/dev/block/by-name/$part bs=4096 count=$blks 2>/dev/null | sha256sum" | tr -d '\r' | cut -d' ' -f1)"
    printf "  %-7s readback=%s  %s\n" "$part" "$rsha" \
           "$( [[ "$rsha" == "${LOCAL_SHA[$img]}" ]] && echo '一致 OK' || echo '!! 不一致' )"
  done
fi

# ---- 清数据 ----------------------------------------------------------------
echo ""
if [[ "$SKIP_WIPE" != "1" ]]; then
  echo "==> 重置 userdata（ext4）与 metadata ..."
  adb_ shell "umount $STAGE 2>/dev/null; rm -f $STAGE/*.img 2>/dev/null; \
              mke2fs -F -t ext4 -b 4096 /dev/block/by-name/userdata 2>&1 | tail -1; \
              dd if=/dev/zero of=/dev/block/by-name/metadata bs=1048576 count=1 2>&1 | tail -1; \
              sync" || echo "  !! 清 data/metadata 有告警，继续"
else
  adb_ shell "rm -f $STAGE/*.img 2>/dev/null; sync"
fi

echo ""
echo "==> 重启到系统 ..."
adb_ reboot 2>&1 || true

cat <<'EOT'

✓ 写入完成。首次开机较慢（重建 /data），等 3~8 分钟。

开机后自检：
  adb shell getprop sys.boot_completed        -> 1
  adb shell getprop ro.product.cpu.abi        -> arm64-v8a
  adb shell getprop ro.zygote                 -> zygote64_32
  adb shell getprop ro.build.version.release  -> 9
  adb shell ls /vendor/lib64                  -> 应列出 29 个文件

若卡第一屏：
  adb logcat -b crash -d | tail -50           # 看 abort message
  adb shell ls -l /vendor/lib64/egl           # 图形驱动是否在
EOT
