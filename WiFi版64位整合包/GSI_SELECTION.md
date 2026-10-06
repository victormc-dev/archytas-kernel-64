# GSI 选型（Archytas / 小爱老师 Wi-Fi 版）

> 2026-10-06 在设备上**实测采集**得出（命令见文末，可复现）。
> 唯一硬约束：**system 分区只有 1,503,232,000 B = 1.40 GiB**。

## 一、Treble 判定结果

| 判定项 | 实测值 | 判据 |
|---|---|---|
| Project Treble | **支持** | `ro.treble.enabled=true` |
| VNDK 版本 | **28** | `ro.vndk.version=28`；`/system/etc/ld.config.28.txt` 存在 |
| VNDK lite 设备 | **否** | `ro.vndk.lite` 为空 |
| CPU 架构 | **arm64** | `ro.product.cpu.abi=arm64-v8a`；`abilist=arm64-v8a,armeabi-v7a,armeabi` |
| 绑定器 | **64 位** | `ro.zygote=zygote64_32`（64 位主 + 32 位副） |
| 分区方案 | **A-only** | `by-name` 里只有 `system`，**无** `system_a`/`system_b`；`ro.boot.slot_suffix` 空；fstab 写 `by-name/system` |
| System-as-root | **是（SAR）** | `mount` 显示 `system` 挂在 `/`；fstab：`by-name/system / ext4 ro` |
| 原厂 vendor | Xiaomi **Archytas**，Android 9 | `ro.vendor.build.fingerprint=Xiaomi/full_Archytas/Archytas:9/PPR1.180610.011/1754:user/release-keys` |
| 动态分区 | 无 `super` | `by-name` 里没有 super |
| 分区加密 | 未加密 | `ro.crypto.state=unencrypted` |

## 二、该下哪类 GSI

phh 系命名：**`system-<arch>-<ab><variant><SU>[-vndklite].img`**

| 字段 | 本机取值 | 理由 |
|---|---|---|
| 架构 | **`arm64`** | 64 位 ARM + 64 位 binder。**不是** `a64`（32 位 userspace），也不是 `arm` |
| 分区 | **`ab`** | 设备是 **SAR**，对应 `b`。当前实际跑的 `phhgsi_arm64_ab` 已实测正常 |
| vndklite | Android 9 → **不带**<br>Android 10+ → **必须带** | vendor 锁死在 VNDK 28 |
| 变体 | `v`=vanilla / `g`=gapps / `o`=Go<br>`N`=无 root / `S`=带 root | 个人偏好 |

### 推荐

**首选（最稳，与设备同代）：Android 9 / SDK 28**

```
system-arm64-ab-vanilla-nosu.img        # ≈ 当前正在运行的构建
```

**要 Android 10 / 11 / 12：普通版就行（有实测证据）**

```
system-arm64-ab-vanilla-nosu.img        # Android 10 的普通版，无需 vndklite
```

拆开 `lineage-17.1-...-bvS.img`（Android 10，**非 vndklite**）实测：

| 事实 | 值 |
|---|---|
| `/system/etc/ld.config.*.txt` | **26 / 27 / 28 / 29 四份齐全** |
| `/system/lib{64}/vndk-{26,27,28,29}` | 自带全部 4 份 |
| `/system/lib{64}/vndk-sp-{26,27,28,29}` | 自带全部 4 份 |

⇒ 普通 GSI 本身就是按「兼容任意 vendor」设计的：linker 按设备的
`ro.vndk.version=28` 选中 **`ld.config.28.txt`**，用 GSI 自带的 `vndk-28` 跑。
**老 vendor + 新 system 不需要 vndklite。**

`vndklite` 真正适用的场景（本机都不满足）：

- 设备 `ro.vndk.lite=true`（VNDK-lite 设备）—— 本机该项**为空**；
- vendor 比 system 更新的场合。

> 上一版文档曾推测「上次 `avS` 起不来是 VNDK 29 对 28 不匹配」，**这个推测已被上面的
> 实测排除**：GSI 自带 vndk-28 与 `ld.config.28.txt`。其真正的失败原因待另行定位。

### 硬约束

| 约束 | 值 |
|---|---|
| 镜像**必须小于** system 分区 | **1,503,232,000 B（1.40 GiB）** |
| 超了怎么办 | 先精简（技能 `gsi-slim-on-device`）：删 `vndk-26/27` 与 `vndk-sp-26/27`。本机实测 `vndk-26` 133 MiB + `vndk-27` 129 MiB（仅 lib/lib64），只留 28 可省 **约 260 MiB** |

### 来源

| 来源 | 说明 |
|---|---|
| **phhusson/treble_experimentations** | `arm64-ab-*` 最全，含 `vndklite`；Android 9 的末版是 **v119** |
| Google 官方 GSI | Android 11 起只剩 `arm64`（ab）一类，面向 Pixel，不建议 |
| LineageOS / crDroid 等社区 GSI | 注意其**是否提供 vndklite**；多数只给普通版 |

## 四、实机刷入验证（2026-10-06）

拿精简后的 **`lineage-17.1-bvS-slim.img`**（Android 10 / ab / vanilla / 带 SU）实刷，
**完全成功**——这是本机第一次跑起 Android 10。

| 项 | 值 |
|---|---|
| 镜像 | `lineage-17.1-bvS-slim.img` — 1,477,640,192 B（分区余 25.6 MB） |
| sha256 | `551dd3fb4ceee11e08dd0db0b6e95c2b52576f2a095b0ffa33b603606186d7b7` |
| system | `fastboot flash system` → 自动分 sparse **12 块** / 106.7 s，全 OKAY |
| 清数据 | `erase userdata` + `metadata` + `cache`（**必须清**，换 system 不清必起不来） |
| boot | **未刷**（沿用 64 位内核 + Wi-Fi DTB 的那份）；GSI 用设备原厂 boot |
| vbmeta | **未动**（此前已 `--disable-verity --disable-verification` 关过校验） |
| 首启 | adb 45 s 上线、**55 s 进桌面**；二次重启同样 55 s，稳定 |

### 刷后实测

| 检查项 | 结果 |
|---|---|
| `ro.build.version.release` / `sdk` | **10** / **29** |
| fingerprint | `Xiaomi/full_Archytas/Archytas:10/QQ3A.200805.001/…:userdebug/test-keys` |
| vendor fingerprint | **仍是原厂 `…:9/PPR1.180610.011/1754:user/release-keys`**（跨版本运行成功） |
| 架构 | `arm64-v8a` / `zygote64_32` |
| VNDK 目录 | `vndk-28` + `vndk-sp-28`（只剩这个版本，精简生效） |
| `ld.config` | `ld.config.28.txt` + `ld.config.vndk_lite.txt` |
| SAR | `/dev/root on / type ext4 (ro,…)` ✅ |
| 关键服务 | surfaceflinger / zygote / audioserver / cameraserver / netd / vold 全 running |
| **Wi-Fi** | `wlan0` UP + **拿到 IP `192.168.1.72/24`**；`wpa_supplicant` running；HAL running；`dumpsys wifi` → `StaEnabledState`→`ConnectedState`→**`CompletedState`**；内核 `halSetFWOwn` / `nicUpdateLinkQuality Rssi=-48` / `mtk_cfg80211_get_station link speed=867` |
| root | `adb root` **不生效**（GSI 的 adbd 不自动提权），但镜像自带 `su`（`/system/xbin/su`，`u:r:phhsu_daemon:s0`）——用 `su -c "…"` |
| 分区容量 | 刷后复核仍是原厂值：system 1,503,232,000 / vendor 419,430,400 / userdata 12,678,315,520 / cache 452,984,832 |
| UI | 截图确认：状态栏（Wi-Fi、信号、电池）、中文设置页、导航栏全正常 |

### 已知 SELinux 拒绝（68 条，无新增致命项）

| 项 | 说明 |
|---|---|
| `nvram_agent_binder` 读 `binder_device`（9 条） | **老遗留**，phh GSI sepolicy 缺 `allow`（Android 9 时同样存在），进程崩溃循环但不影响使用 |
| `vendor_init` 写 `/data`、`search misc/wifi/drm`（约 11 条） | GSI 的 vendor_init 策略与原厂 `/data` 布局差异；**实测不影响 Wi-Fi 连接与开机** |
| `vold` 写 `sysfs_mmcblk` uevent、`RenderThread` 读 `debugfs_ion`、`dmesg syslog_read` 被拒 | 正常/无害 |
| ANR | `com.android.gallery3d` 在 `BOOT_COMPLETED` 广播超时（首启 CPU 被 Settings 占用），**一次性，无害** |

### 结论

1. **本机要用 `b`（ab）变体，`a`（aonly）变体此前实测起不来** —— 与设备 **SAR=是** 一致。
   同批次的 `lineage-17.1-…-treble_arm64_avS.img` 上次刷入后无法启动，而本次 `bvS` 顺利开机。
2. **不需要 vndklite**（见第二节实测）。
3. Android 10 GSI 可与原厂 Android 9 vendor 跨版本共存运行（vendor fp 1754 未变）。
4. 回退路径：`fastboot flash system WiFi版64位整合包/img/system.img` + 三清（换回 Android 9+ 原厂 vendor）。

## 三、采集方法（可复现）

```bash
A=adb   # 本机：D:/Software/i4Tools9/files/adb/adb.exe
$A shell 'getprop ro.treble.enabled; getprop ro.vndk.version; getprop ro.vndk.lite;
          getprop ro.product.cpu.abilist; getprop ro.zygote;
          getprop ro.boot.slot_suffix; getprop ro.build.system_root_image;
          getprop ro.vendor.build.fingerprint'
$A shell ls /dev/block/by-name/          # 有无 system_a/_b → A/B 判定
$A shell mount | grep ' / '              # system 是否挂 / → SAR 判定
$A shell cat /vendor/etc/fstab.mt6761    # system → / 即 SAR
$A shell ls -d /system/lib64/vndk-*      # GSI 自带的 VNDK 版本
```
