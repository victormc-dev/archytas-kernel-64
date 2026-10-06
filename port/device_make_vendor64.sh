#!/system/bin/sh
# ============================================================================
# device_make_vendor64.sh —— 在 Archytas 真机上产出「64 位适配版 vendor.img」
#
# 为什么在设备上做：Windows 主机没有可用的 ext4 写工具（无 WSL、无 e2fsprogs），
# 而设备自带完整 e2fsprogs（mke2fs/e2fsck/tune2fs）与 losetup，可以
#   cp 一份原厂镜像 -> losetup 挂到 /dev/block/loop0 -> mount 改文件 -> umount
#   -> e2fsck -fy
# 这样【不动线上 /vendor】，产出的是干净可 flash 的镜像。
#
# 输入（由主机 push 到 $W）：
#   $W/base.img            原厂 vendor 镜像（400 MiB ext4，build 1754）
#   $W/lib64src/**.so      64 位图形/显示栈（含 hw/、egl/）
#   $W/ko/*.ko             64 位连通性模块（wmt_drv/wmt_chrdev_wifi/wlan_drv_gen4m/bt_drv）
# 输出：
#   $W/vendor_out.img      适配后的 vendor 镜像
#
# 用法： adb shell sh /data/local/tmp/wifi64/device_make_vendor64.sh
# ============================================================================

W=/data/local/tmp/wifi64
LOOP=/dev/block/loop0
MNT=/mnt/vb64
IMG=$W/vendor_out.img
FAIL=0

echo "########## 0. 前置检查 ##########"
for f in $W/base.img; do
  [ -f "$f" ] || { echo "!! 缺输入: $f"; exit 1; }
done
ls -l $W/base.img
echo "-- 线上 /vendor 的 32 位同名的上下文（作为 lib64 的标签来源）--"
ls -Z /vendor/lib/libusc.so /vendor/lib/hw/gralloc.mt6761.so /vendor/lib/egl/libEGL_mtk.so 2>&1

echo
echo "########## 1. 复制基座镜像 ##########"
cp -f $W/base.img $IMG || exit 1
ls -l $IMG

echo
echo "########## 2. loop 挂载 ##########"
losetup $LOOP $IMG || { echo "!! losetup 失败"; exit 1; }
mkdir -p $MNT
mount -t ext4 -o rw $LOOP $MNT || { echo "!! mount 失败"; losetup -d $LOOP; exit 1; }
echo "-- 挂载后空间 --"
df -h $MNT 2>/dev/null | tail -1

echo
echo "########## 3. 注入 /lib64 ##########"
mkdir -p $MNT/lib64/hw $MNT/lib64/egl
cp -f $W/lib64src/*.so      $MNT/lib64/      2>/dev/null || true
cp -f $W/lib64src/hw/*      $MNT/lib64/hw/   2>/dev/null || true
cp -f $W/lib64src/egl/*     $MNT/lib64/egl/  2>/dev/null || true
chmod 0644 $MNT/lib64/*.so $MNT/lib64/hw/* $MNT/lib64/egl/*
chown 0:0 $MNT/lib64 $MNT/lib64/hw $MNT/lib64/egl
chown 0:0 $MNT/lib64/*.so $MNT/lib64/hw/* $MNT/lib64/egl/*
echo "-- 注入结果 --"
ls -l $MNT/lib64/ $MNT/lib64/hw/ $MNT/lib64/egl/ | grep -v '^total' | head -45

echo
echo "########## 4. 替换 64 位连通性模块 ##########"
for m in wmt_drv.ko wmt_chrdev_wifi.ko wlan_drv_gen4m.ko bt_drv.ko; do
  if [ -f "$W/ko/$m" ]; then
    cp -f $W/ko/$m $MNT/lib/modules/$m && echo "   replaced $m"
  else
    echo "   !! 缺 $W/ko/$m"; FAIL=1
  fi
done
chmod 0644 $MNT/lib/modules/*.ko
chown 0:0 $MNT/lib/modules/*.ko
ls -l $MNT/lib/modules/

echo
echo "########## 5. default.prop: ro.zygote -> zygote64_32 ##########"
echo "-- 改前 --"; grep '^ro.zygote' $MNT/default.prop
sed -i 's/^ro\.zygote=zygote32$/ro.zygote=zygote64_32/' $MNT/default.prop
echo "-- 改后 --"; grep '^ro.zygote' $MNT/default.prop
grep -q '^ro\.zygote=zygote64_32$' $MNT/default.prop || { echo "!! ro.zygote 未改成功"; FAIL=1; }

echo
echo "########## 6. SELinux 上下文（镜像 32 位同名文件）##########"
# 线上 /vendor 已按 vendor_file_contexts/plat_file_contexts 打好标签，
# 直接引用同名 32 位文件的上下文最可靠（chcon --reference 在 toybox 上不一定有）。
# 改标签需要 relabelto 权限；Enforcing 下 shell 域可能被拒，故临时置 permissive。
# 注意 getenforce 在 Android 上返回的是 "Enforcing"/"Permissive" 文本（不是 1/0），
# 早期版本拿它跟 "1" 比较导致永远不恢复 enforcing —— 这里用 case 匹配两种写法。
OLD_ENF=$(getenforce 2>/dev/null)
case "$OLD_ENF" in
  Enforcing|1) NEED_ENFORCE=1 ;;
  *)           NEED_ENFORCE=0 ;;
esac
setenforce 0 2>/dev/null || true

# 目录本身
for pair in "$MNT/lib64:/vendor/lib" "$MNT/lib64/hw:/vendor/lib/hw" "$MNT/lib64/egl:/vendor/lib/egl"; do
  dst="${pair%%:*}"; ref="${pair##*:}"
  ctx=$(ls -Zd "$ref" 2>/dev/null | awk '{print $1}')
  [ -n "$ctx" ] && chcon "$ctx" "$dst" && echo "   dir $dst <- $ctx"
done

for sub in "" "/hw" "/egl"; do
  for f in $W/lib64src$sub/*; do
    [ -f "$f" ] || continue
    n=$(basename "$f")
    ref="/vendor/lib$sub/$n"
    ctx=$(ls -Z "$ref" 2>/dev/null | awk '{print $1}')
    if [ -n "$ctx" ]; then
      chcon "$ctx" "$MNT/lib64$sub/$n" 2>/dev/null && echo "   $sub/$n <- $ctx" \
        || { echo "   !! chcon 失败 $sub/$n"; FAIL=1; }
    else
      echo "   ?? 无 32 位参照: $sub/$n（保留默认上下文）"
    fi
  done
done

[ "$NEED_ENFORCE" = "1" ] && setenforce 1 2>/dev/null
echo "-- enforcing: 原=$OLD_ENF 现=$(getenforce 2>/dev/null) --"

echo
echo "########## 7. 校验标签 ##########"
ls -Z $MNT/lib64/ | head -6
echo "  ---"
ls -Z $MNT/lib64/hw/
echo "  ---"
ls -Z $MNT/lib64/egl/

echo
echo "########## 8. 卸载 + fsck ##########"
sync
umount $MNT || { echo "!! umount 失败"; FAIL=1; }
losetup -d $LOOP 2>/dev/null || true
e2fsck -fy $IMG 2>&1 | tail -8

echo
echo "########## 9. 结果 ##########"
ls -l $IMG
if [ "$FAIL" = "1" ]; then echo "########## RESULT: FAIL ##########"; else echo "########## RESULT: OK ##########"; fi
