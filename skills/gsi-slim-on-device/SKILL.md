---
name: gsi-slim-on-device
description: 把过大的 Android GSI / system 镜像精简到小于目标 system 分区容量，使其能刷入。本机（Windows + WorkBuddy）没有 e2fsprogs 且 WSL 被安全策略禁用，所以**把已 root 的 Android 设备当作 ext4 工具宿主**：展开 sparse → push → losetup/mount → du 盘点 → 删冗余 → 卸载 → e2fsck → resize2fs -M → truncate → 拉回。含最关键的「按 ro.vndk.version 删多余 VNDK」判据，以及一串本机专属坑（toybox `losetup -f` 坏、e2fsck 打不开 loop 设备、ro→rw 重挂 EINVAL、MSYS 路径转换、device 工具清单）。Trigger words: GSI 精简, GSI 太大, 刷不进去, system 分区放不下, system.img 太大, resize2fs, ext4 缩容, 瘦身, vndk 删除, treble GSI slim, slim GSI, shrink system image, vndk-26 vndk-27 vndk-29.
agent_created: true
---

# 把 GSI 精简到能刷进 system 分区

## 适用场景与核心思路

症状：一份 GSI / system 镜像比设备 system 分区大，`fastboot flash system` 写不下。
要解决必须**真的删文件 + 缩容 ext4**，不是压缩。

本机的约束：
- **没有** `sim2img/e2fsck/resize2fs/mke2fs/debugfs`（Git Bash 里一个都没有）；
- **WSL 被安全策略禁用**（`Program Blacklist`，不可绕过，别重试）；
- 所以：**把已 root 的 Android 设备当 ext4 工具宿主**。Android 自带
  `/system/bin/{mke2fs,e2fsck,resize2fs,tune2fs,mkfs.ext4,losetup,truncate,blkid}`。

本地只负责：展开稀疏镜像、盘点、拉回。

## 第 0 步：先算清楚差多少

```bash
# 目标分区真实容量（不要靠文档，问设备）
adb shell 'd=$(ls -l /dev/block/by-name/system | sed "s/.*-> //"); blockdev --getsize64 $d'
# 1433600 → 1503232000 B
```

镜像侧读超级块：`blocks * block_size` 才是 fs 真实大小（镜像文件常带尾部填充）。

**注意块对齐**：`1,503,232,000 / 4096 = 367,000` 整除，所以最大可用 367,000 块。
算「需要净释放多少块」= 当前块数 − 目标块数，再扣掉已有的空闲块。

## 第 1 步：展开 sparse（若魔数是 `3a ff 26 ed`）

```bash
python port/sparse.py expand <sparse.img> <raw.img>
```
`ext4_read.py` 也能直接读 `SparseFile` 对象，但后面要 push 到设备，还是展开成 raw。

## 第 2 步：盘点（本地，不挂载）

```bash
python port/ext4_du.py <raw.img> / 1        # 顶层
python port/ext4_du.py <raw.img> /product 1 # 逐层下钻
```
按已分配 extent 块统计，稀疏文件不会被冤枉。**先盘点再动手**，否则只能瞎猜。

经验值（Android 10 LineageOS 17.1 GSI，1.8 GB）：
`/product` ≈ 474 MiB（其中 `app/webview` 单文件 156 MiB、`app/LatinIME` 62 MiB）、
`/lib64` ≈ 426、`/lib` ≈ 296、`/framework` ≈ 231、`/apex` ≈ 131。

## 第 3 步：找出可删项 —— 最大一条杠杆是多余的 VNDK

GSI 为兼容**所有** vendor，会同时带 VNDK 26/27/28/29 的完整副本；设备只需要
**跟自己 vendor 版本匹配的那一份**。这一项通常就是 300–400 MiB。

**判据（必须两条都核实，别凭印象）**：

```bash
adb shell getprop ro.vndk.version            # 设备侧真实取值
# 镜像侧：配置按版本命名，看它引用谁
adb shell "grep -o '/system/\${LIB}/vndk[a-z-]*-[0-9]*' \$G/etc/ld.config.28.txt | sort -u"
#   → /system/${LIB}/vndk-28
#   → /system/${LIB}/vndk-sp-28
```

`/etc/ld.config.<N>.txt` 存在且 linker 按 `ro.vndk.version` 选文件 ⇒ 其它版本是
**不可达的死重**，可连同 `ld.config.<N>.txt` 一起删：

```sh
for v in 26 27 29; do
  rm -rf $G/lib/vndk-$v $G/lib/vndk-sp-$v $G/lib64/vndk-$v $G/lib64/vndk-sp-$v
  rm -f  $G/etc/ld.config.$v.txt
done
```
⚠️ **保留 `/lib/vndk-28` 与 `/lib/vndk-sp-28`**（32 位也要）——vendor 的 32 位
进程（HAL service）同样靠它。`libc.so` 在 **vndk-sp**，不在 vndk。

其它可选（按影响从小到大）：
`libclang_rt.asan-*.so`（sanitizer 运行时）→ `libLLVM_android.so`（只影响 RenderScript）
→ 与设备无关的应用（Wi-Fi 平板删 `messaging/Email/Exchange2/Dialer/Contacts/EmergencyInfo`）
→ 娱乐类（`Eleven/Gallery2/Etar/Jelly/Recorder`）。

**不要删**：`webview`（应用会崩）、`LatinIME`（没输入法）、`Settings/SystemUI/launcher`。
删应用后遗留的 `/product/etc/permissions/*.xml` 只是 `privapp-permissions` 白名单，
对未安装包是**惰性条目，无害**，不必清理。

## 第 4 步：在设备上挂载、删、缩容

推镜像（**注意 MSYS 路径转换**）：
```bash
export MSYS_NO_PATHCONV=1      # 否则 /data/... 会被改写成 Windows 路径
adb push "D:/path/raw.img" /data/local/tmp/raw.img   # 源用 Windows 风格路径
```
> `MSYS_NO_PATHCONV=1` 会**同时**禁掉源路径的转换，所以源路径必须写成 `D:/...`，
> 不能写 `/d/...`。两者只能选一个风格。

挂载（`losetup -f` 在本机 toybox 上**是坏的**，会报 `No such device or address`；
必须显式指定 loop 设备）：
```sh
losetup /dev/block/loop0 /data/local/tmp/raw.img
mount -t ext4 -o rw /dev/block/loop0 /data/local/tmp/gsi
```
> **ro→rw 重挂会 EINVAL**（`SELinux: mount invalid. Same superblock, different
> security settings`）。必须先 `umount` + `losetup -d` 解绑再重新 `losetup`，
> 让旧 superblock 被丢弃。

盘点与删除用设备原生工具（`du -sk` 交叉验证本地数字），然后卸载。

缩容关键顺序：
```sh
umount $G
losetup -d /dev/block/loop0          # 解绑，避免挂载态/loop 干扰
e2fsck -f -y /data/local/tmp/raw.img      # ★ 直接对「文件」，不要对 /dev/block/loopN
resize2fs -M /data/local/tmp/raw.img      # 缩到最小
tune2fs -l /data/local/tmp/raw.img | grep -E 'Block count|Free blocks|state'
truncate -s $((blocks*4096)) /data/local/tmp/raw.img
e2fsck -f -y /data/local/tmp/raw.img      # 复核
```
> **`e2fsck`/`resize2fs` 对 `/dev/block/loopN` 会报
> `Invalid argument while trying to open`**（loop 上 O_DIRECT 对齐问题）。
> **改成直接传镜像文件路径**即可，e2fsprogs 原生支持普通文件。

`resize2fs -M` **能搬移数据块**，所以「删掉文件」未必够 —— 还需要「删完总量低于
目标」。删完记得看 `Free blocks`，缩容后的块数要留几 MiB 余量给元数据/组边界。

## 第 5 步：验证 + 拉回

拉回后用**两条独立路径**验证，别只信一条：
1. 设备侧：重新 `losetup` + `mount -o ro`，`ls` 关键文件、`df` 容量；
2. 本地侧：`python port/ext4_read.py <img> list /`（纯 Python 独立解析）。

判据：`文件字节数 ≤ 分区容量`、`tune2fs` 显示 `state: clean`、
`ro.vndk.version` 对应目录仍在、被删项确实消失。

## 第 6 步：刷入后验证（实测要点）

`fastboot flash system <slim.img>` → `erase userdata metadata cache` → `reboot`。
（**换 system 必须三清**，不清必起不来。别刷 boot、别动 vbmeta。）

GSI 上的环境与原厂 ROM **不一样**，验证时注意：

- **`adb root` 在 GSI 上无效**：命令无输出、`wait-for-device` 后 `id` 仍是
  `uid=2000(shell)`。GSI 自带 `/system/xbin/su`（`u:r:phhsu_daemon:s0`，实测可直接用）
  ⇒ 需 root 的命令一律走 `su -c "…"`。
- **SELinux 更严**：`dmesg`（klogctl）、`blockdev` 读 by-name 都需要 root；
  非 root 下取分区尺寸改读 `/sys/class/block/*/size`，网卡列表读 `/proc/net/dev`。
- ★★ **变体必须选对**：设备是 **SAR** 就要用 **`b`/ab** 变体，别用 `a`/aonly。
  实测同一份 LineageOS 17.1：`avS`（aonly）刷入起不来，换 `bvS`（ab）直接开机。
- 首启判据：`ro.build.version.release` 符合预期、`ro.vendor.build.fingerprint`
  仍是原厂（跨版本共存正常）、`vndk-<N>` 只剩一份、关键服务
  （surfaceflinger / zygote / audioserver / netd / vold）全 `running`、
  Wi-Fi 能拿到 IP 并实际打开网页。

## 踩坑清单

| 坑 | 说明 |
| --- | --- |
| `losetup -f` 报 `No such device or address` | 本机 toybox 的探测坏了，但 `/dev/block/loop0..7` 都在。**显式指定**设备即可 |
| `e2fsck: Invalid argument while trying to open /dev/block/loopN` | loop 设备上的 O_DIRECT 问题。**改成对镜像文件操作** |
| `mount -o rw` 报 `Invalid argument` | 之前 ro 挂过、superblock 被钉住。先 `losetup -d` 再挂 |
| `adb push` 目的地变成 `C:/.../data/local/tmp/...` | MSYS 路径转换。`export MSYS_NO_PATHCONV=1`，源路径写 `D:/...` |
| `dd: block size '4M': illegal number` | TWRP 的 toybox dd **不接受 `K/M/G` 后缀**，且报错后**静默什么都不写**。改纯数字 `bs=4194304`。详见技能 `twrp-partition-restore` |
| `python x.py <img> /` 里 `/` 被吃成 Windows 根 | 同样禁用转换：`MSYS_NO_PATHCONV=1 python ...` |
| SELinux 拒绝挂载/删除 | 设备若可 `setenforce 0` 临时放宽；**做完记得 `setenforce 1` 复位** |
| `resize2fs` 缩不到期望值 | 它只能缩到「最后一个在用块」附近（会搬块但有边界）。**继续删**，或先 `e2fsck -f` |
| 文件系统没有日志（dmesg: `mounted filesystem without journal`） | GSI 常见（`make_ext4fs` 不建 journal）。`e2fsck`/`resize2fs` 仍正常，且更快 |
| `[ -e symlink ]` 总是 false | 符号链接指向绝对路径（如 `/apex/...`），在宿主里**不会**按挂载点解析。改用 `ls -l` |

## 一次真实操作的结果（可作参照）

LineageOS 17.1 `treble_arm64_avS`（sparse 1,941,131,520 → raw 2,003,943,424 B，
fs 481,504 块）→ 目标分区 1,503,232,000 B（367,000 块）：

- 删 VNDK 26/27/29（lib+lib64+sp）**400.4 MiB** + 5 个通信类应用 **53.4 MiB**
  ⇒ 已用 1,865,720 → 1,400,932 KiB；
- `e2fsck -f` → `resize2fs -M` → **360,767 块 = 1,477,701,632 B**，
  比分区小 24.3 MiB，`state: clean`；
- 全程约 10 分钟（含 2 次 1.5–2 GB 的 adb 传输，~14 MB/s）。
