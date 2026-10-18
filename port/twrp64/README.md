# Archytas 64 位 TWRP(recovery-archytas-arm64.img)

为小米小爱老师 Wi-Fi 版(Archytas, MT6761)组装的 **64 位 TWRP recovery 镜像**。

## 为什么需要

现役 TWRP 3.7.0_9-0 是 **32 位用户态**(`ro.product.cpu.abilist=armeabi-v7a,armeabi`,abilist64 为空)。刷 `BiTGApps-arm64-*` zip 时,包内 busybox 是 64 位 ELF,32 位 TWRP 无法执行:

```
/tmp/updater[64]: /tmp/busybox-arm: not executable: 64-bit ELF file
Updater 过程结束,错误: 1
```

## 组包方案(组装,非全量编译)

| 部件 | 来源 |
|---|---|
| kernel | `boot-archytas-wifi.img`(GitHub Actions 构建,Image.gz-dtb 已内嵌 Wi-Fi DTB,ELF 64-bit ARM aarch64) |
| ramdisk 用户态 | 小米 Play(lotus, 同 MT6761)arm64 TWRP 3.5.2:init/recovery 均为 64-bit aarch64,interpreter `/sbin/linker64` |
| recovery.fstab | 现役 Archytas TWRP 解包所得(by-name:system/vendor/data/cache/…,35 条) |

ramdisk 合成 = lotus 全量复制 + 覆盖 `etc/recovery.fstab`(Archytas)+ 删除 `miui.factoryreset.{fstab,rc}`。

打包字段沿用 boot-archytas-wifi.img:`base=0x40078000`、`cmdline="bootopt=64S3,32S1,64S1 buildvariant=userdebug"`、`header_version=1`、`pagesize=2048`、`hashtype=sha1`。

## 重新组装

```bash
git clone https://github.com/osm0sis/mkbootimg && cd mkbootimg && make   # 一次
./build_twrp64.sh boot-archytas-wifi.img TWRP--lotus-20211005-Unofficial_v1.1.img recovery.img
```

三个输入:
1. `boot-archytas-wifi.img` — Actions artifact `archytas-wifi-boot-and-modules`(run 39097516731)
2. `TWRP--lotus-20211005-Unofficial_v1.1.img` — https://github.com/gokur20/twrp_xiaomi_lotus_-Mi-Play-/releases/download/v1.1/… (22,247,424 B)
3. `recovery.img` — 现役 32 位 TWRP(33,554,432 B),只取其中 `etc/recovery.fstab`

## 刷入

任选其一:
- `fastboot flash recovery recovery-archytas-arm64.img`
- 现役 TWRP → Install → Install Image → 选 `recovery-archytas-arm64.img` → Recovery
- root 后 `dd if=recovery-archytas-arm64.img of=/dev/block/platform/bootdevice/by-name/recovery`

刷入后建议 **双清(Wipe → Format Data)** 再刷 `BiTGApps-arm64-12.0.0-*CORE.zip`。

## 验证

- `file recovery-archytas-arm64.img` → `Android bootimg, kernel (0x40080000), ramdisk (0x51b00000), page size: 2048`
- `unpackbootimg -i recovery-archytas-arm64.img -o out/` → header_version 1,cmdline 64S3/32S1/64S1
- 解包 ramdisk:init/recovery 均为 `ELF 64-bit LSB, ARM aarch64`,fstab 为 Archytas 版

## 注意事项

- 屏幕/触摸/按键依赖内核 DTB,64 位内核已内嵌 Wi-Fi DTB,理论上正常;若触摸/按键异常,需回到设备树编译路线。
- 未验证项:实机刷入与 BiTGApps 刷入。已做结构级验证(架构/fstab/打包字段)。

---

# v2: Actions 全量构建 TWRP 3.7.1 arm64(工具全升级)

2026-10-18 起,改用 **GitHub Actions 全量编译最新 TWRP**,不再用 lotus 3.5.2 旧用户态组装。
产出的 recovery 内置工具全部为**全新源码编译的 64 位 ELF**(toybox / awk / bc / e2fsck / mke2fs / resize2fs / tune2fs 等)。

## 产物

- release: `twrp-arm64-39117090618`(asset `recovery.img`, 29,554,688 B, md5 `ae3e14565c81db5b990a27fd2000dff6`)
- 验证:解包 `recovery.img` →
  - kernel 段 10,475,913 B,与 `boot-archytas-wifi.img` 内核**字节级一致**(gzip Image.gz-dtb,自带 Wi-Fi DTB)
  - `system/bin/recovery` ELF64 aarch64,版本串 **3.7.1**
  - `system/bin/{toybox,awk,sh,bc,e2fsck,mke2fs,resize2fs,tune2fs}` 全 ELF64 aarch64
  - `system/etc/recovery.fstab` = Archytas 35 条 by-name 分区表
  - 打包字段:base=0x40078000, ramdisk_offset=0x11a88000, tags_offset=0x07808000, header v1, cmdline `bootopt=64S3,32S1,64S1 buildvariant=userdebug`

## 构建方法(全量)

| 部件 | 来源 |
|---|---|
| manifest | minimal-manifest-twrp/platform_manifest_twrp_aosp @ **twrp-12.1** |
| 设备树 | `victormc-dev/twrp_device_xiaomi_Archytas`(fork 自 ZH-XiJun,arm64 改造) |
| kernel | prebuilt/kernel = boot-archytas-wifi.img 内核段(64 位 Image.gz-dtb) |
| workflow | `archytas-kernel-64` 仓库 `.github/workflows/twrp-build.yml` |

一键触发:

```bash
gh workflow run "TWRP Build (Archytas arm64)"
```

### arm64 改造要点(设备树 fork 的提交)

1. `TARGET_ARCH := arm64`(armv8-a / arm64-v8a),内核换 64 位 Image.gz-dtb,去分离 DTBO
2. `omni_Archytas.mk` 继承改 `vendor/twrp/config/common.mk`(minimal manifest 无 vendor/omni)
3. `TARGET_SUPPORTS_64_BIT_APPS := true`(AOSP 12 board_config 检查;32 位开关无效)
4. **`BOARD_RECOVERYIMAGE_PARTITION_SIZE` 必须定义** —— AOSP 12 靠它设置 `BUILDING_RECOVERY_IMAGE`,缺失 → `recoveryimage` 空目标 → `ninja: no work to do`(recovery.img 不生成,构建假成功)
5. `TARGET_RECOVERY_FSTAB := $(DEVICE_PATH)/recovery.fstab`(装入 `system/etc/recovery.fstab`)

## 实机刷入

```bash
adb reboot bootloader   # 或设备关机 → 音量- + 电源
fastboot flash recovery recovery.img
fastboot reboot recovery   # 直接进 TWRP
```

> 注:recovery.img 28.2 MiB;若设备 recovery 分区不足,先 `fastboot getvar current-slot`/分区大小确认,必要时刷入 boot 分区配合 `fastboot boot recovery.img`(临时启动,不落盘)。
