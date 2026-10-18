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
