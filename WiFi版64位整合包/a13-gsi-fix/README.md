# A13 GSI 适配:sepolicy 补丁 + vendor /data 修复

针对 **小爱老师 Wi-Fi 版(Archytas,MT6761)** 刷 Android 13 GSI
(`LineageOS 20` / Andy Yan `lineage-20-light` 分组)时的**两个独立启动故障**的修复包。

---

## 故障 1:surfaceflinger 崩溃循环(SELinux 权限丢失)

**现象**:启动 16.5s 后 SF 的 RenderEngine 线程 SIGABRT,`Abort message: output buffer not gpu writeable`。

**根因**(源码级证据):
1. A13 `RenderEngine.cpp:61-65` `validateOutputBufferUsage`:`FATAL_IF(!(getUsage() & USAGE_HW_RENDER))`;
2. `GraphicBuffer.cpp:196`:`initWithSize` 仅当 `allocator.allocate()` 成功才写入 `usage`——分配失败 → usage=0;
3. 崩的 buffer 来自 `RenderEngineThreaded::primeShaderCache`(`Cache.cpp:338-368`,128×128 RGBA_8888,usage=`HW_RENDER|HW_TEXTURE`)——请求正确,是**分配失败**;
4. 分配失败原因:MTK gralloc/EGL 依赖 `/proc/ged`(GED GPU 扩展驱动,反汇编 `libgralloc_extra.so`/`libIMGegl.so` 见 `ged_ge_alloc`/`ged_swd_*`),日志高频 `avc: denied { ioctl }` `/proc/ged`(scontext=surfaceflinger);
5. **为什么 GSI 才崩**:MTK 把 surfaceflinger 的**全部权限**编译在自研 platform sepolicy(system 分区)里,vendor_sepolicy.cil 里 SF 的 allow 一条都没有;刷 GSI 换掉 system 即丢失(A10/A13 GSI 的 plat_sepolicy.cil 均无 proc_ged,与版本无关,也是此前 `setenforce 0` 才有效的原因)。

**修复**:GSI `/system/etc/selinux/plat_sepolicy.cil` 末尾追加 `(typepermissive surfaceflinger)`。
userdebug 镜像的 init 每次启动用自带 secilc 重编译 sepolicy,改 cil 即生效,无需动预编译 sepolicy。

```bash
./patch_gsi_sepolicy.sh lineage20_light.img lineage20_light_patched.img
# 刷机
fastboot flash system lineage20_light_patched.img
```

**产物**(见下方 Release):`lineage20_light_patched.img.xz`
sha256:`48e319136b3063b7cfa76fd0a25126f1ce56ee2df06db50c93b55e9d01243138`(解压后 img)

---

## 故障 2:/data 挂不上(FDE → FBE)

**现象**:sepolicy 修好后 SF 能起,但 `zygote` 起不来,adb 全程无影子——`/data` 没挂。

**根因**:vendor `fstab.mt6761` 的 userdata 行仍是 Android 9 时代的 **FDE** 写法
`encryptable=/dev/block/platform/bootdevice/by-name/metadata,`;
Android 11+ 已移除 FDE 支持,A13 init/fs_mgr 无法按该写法挂载。

**修复**:改为 FBE 写法(本机有 keymaster@3.0):

```bash
./patch_vendor_fstab.sh vendor.img vendor_fixed.img
# 刷机
fastboot flash vendor vendor_fixed.img
```

userdata 行从
`...,resize,encryptable=/dev/block/platform/bootdevice/by-name/metadata,`
改为
`...,resize,fileencryption=aes-256-xts:aes-256-cts`

**产物**:`vendor_fixed.img`
sha256:`e796b6b1e5188fdde9ed7df1d1c3e6c6014a7da1698ca9b5dc045af8f52746ab`

---

## 刷机顺序(Windows,system 已按 `resize_system_3gb.sh` 扩到 3 GiB)

```bat
fastboot flash system lineage20_light_patched.img
fastboot flash vendor vendor_fixed.img
fastboot format userdata    :: FDE→FBE 切换,旧 /data 必须清(会丢数据)
fastboot reboot
```

**预期**:首次启动较慢(bootanimation 多转几圈)。进桌面 = 两个故障都已修复。

## 判别实验(30 秒)

怀疑根因时:刷任意 GSI 后 `adb root && adb shell setenforce 0 && adb reboot`,
SF 不再崩 → 确认是 SELinux 权限问题,直接走故障 1 的补丁。

## 配套证据

- 崩溃日志:设备 pstore / `logcat -d | grep "avc: denied"`(高频 `proc_ged` ioctl 拒绝)
- A13 源码:`frameworks/native/libs/renderengine/RenderEngine.cpp`、`libs/ui/GraphicBuffer.cpp`、`libs/renderengine/skia/Cache.cpp`
- vendor sepolicy:`debugfs -R "dump /etc/selinux/vendor_sepolicy.cil <dst>" vendor.img`(SF 零规则)
