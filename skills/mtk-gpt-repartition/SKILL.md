---
name: mtk-gpt-repartition
description: 在只有 TWRP(recovery)、没有 parted/gdisk/python、且 WSL 被禁的本机环境下，给 MediaTek 设备**改 GPT 重分区**（扩容/缩小 system、vendor、userdata 等）。核心是"目标分区后面还压着别的分区"时必须整体平移、以及 TWRP 那个只认长选项的裁剪版 sgdisk。含零风险探测 sgdisk 支持哪些选项、连续性预检、改表后内核分区表失效的应对、必须从 /tmp 运行的坑、以及 **TWRP 的 /sbin/sh 是 32 位算术**（会让"算完再比上界"的守卫被乘出负数绕过）。Trigger words: 扩容 system 分区, 扩大分区, 重分区, 改分区表, GPT, sgdisk, 分区不够大, repartition, resize partition, lk2, scatter, mmcblk0p, TWRP 分区, 刷不进, not enough space, 32位算术, 整数溢出.
agent_created: true
---

# MTK 设备在 TWRP 里改 GPT 重分区

## 0. 先决判断：目标分区后面还有没有别的分区

GPT 里分区是**连续排布**的。要放大分区 #N，只能吃掉它**紧后面**的空隙。
如果 #N+1、#N+2 首尾相连（MTK 原厂 ROM 基本都是满排），那么"扩容 #N"实际等于
**把后面那一串整体向后平移 delta = 新大小 − 旧大小**。

**平移会作废被平移分区的"内容"**——它们的数据还留在旧 LBA 上，新 LBA 是别的分区
的旧数据。所以：

| 被平移的分区 | 后果 | 能不能救 |
|---|---|---|
| 可丢弃的（cache、空白分区） | 无所谓 | 清掉头 1 MiB，靠 fstab 的 `formattable` 让首启自动重建 |
| 有内容的（userdata、vbmeta） | **数据全丢** | userdata 救不了（分区起点变了，旧文件系统不可能原地可用）；体积小的可以先备份、改表后写回新位置 |
| **目标分区自己** | **毫无影响** | 起始 LBA 没变，内容原样保留，不需要重刷 |

> ★ 所以"扩容 system"几乎必然等于"清空 /data"。这是分区连续性的硬约束，不是取舍。
> 先跟用户说清楚，别默默把数据抹了。

## 1. 侦察（全部只读）

```bash
ADB=<adb 路径>
# ① 分区名 → 设备节点
adb shell 'ls -l /dev/block/by-name/'
# ② 真实 LBA 布局（512B 扇区；这是 kernel 视图，和 GPT 一致）
adb shell 'for d in /sys/class/block/mmcblk0p*; do n=${d##*/}; echo "$n $(cat $d/start) $(cat $d/size)"; done'
# ③ 磁盘总扇区
adb shell 'cat /sys/class/block/mmcblk0/size'
# ④ 权威 GPT（有 sgdisk 时用它；注意可能是裁剪版，见 §2）
adb shell 'sgdisk --print /dev/block/mmcblk0'
# ⑤ 分区唯一 GUID（要保留就抄下来）
adb shell 'sgdisk --info=31 /dev/block/mmcblk0'
```

拿 start/size 自己验证"是否首尾相连"：`pN.start + pN.size == p(N+1).start`。

## 2. ★ TWRP 的 sgdisk 是裁剪版：只认长选项

TWRP（如 3.5.2）自带 `/sbin/sgdisk`（gptfdisk 0.8.10.2），但短选项被去掉了：

```
$ sgdisk -p /dev/block/mmcblk0
sgdisk: invalid option -- p      <-- 短选项全废
$ sgdisk --print /dev/block/mmcblk0
（正常输出）                      <-- 长选项可用
```

**零风险探测某选项是否支持**——拿一个不存在的设备去喂它，只走参数解析：

```bash
for o in "--delete=1" "--new=1:100:200" "--typecode=1:0700" \
         "--change-name=1:foo" "--partition-guid=1:11111111-2222-3333-4444-555555555555" \
         "--backup=/tmp/x.bin" "--load-backup=/tmp/x.bin"; do
  r=$(sgdisk $o /nonexistent-dev 2>&1 | head -1)
  case "$r" in
    *"unrecognized option"*) echo "不支持  $o";;
    *)                       echo "支持    $o";;
  esac
done
```

回 `Problem opening /nonexistent-dev for reading!` = 支持；回 `unrecognized option` = 不支持。
**这个方法对任何"选项是否被裁剪掉"的疑问都适用，且绝对不改盘。**

已知 TWRP 里没有：`parted` / `gdisk` / `sfdisk` / `fdisk` / `partx` / `python`。
有的：`mke2fs` / `e2fsck` / `resize2fs` / `tune2fs` / `blkid` / `blockdev` / `dd` /
`awk` / `sed` / `grep` / `sort` / `printf` / `sha256sum`；`/sbin/sh` 支持 `$(( ))`。

## 2b. ★ TWRP 的 `/sbin/sh` 是 **32 位算术**

最容易踩空的一条。TWRP 的 `/sbin/sh`（`sh` 与直接 `/sbin/sh` 都一样）用**有符号 32 位**
做 `$(( ))`，实测：

```
$((2147483647+1))  = -2147483648
$((99999999*2048)) = -1358432256      # 负数！
$((25795392*512))  = 322338816        # 应为 13207240704
$((14029*2048))    = 28731392         # ✓ 没越 2^31 才正确
```

后果：凡是"先算再和上界比"的**守卫都能被绕过**——乘出负数会让 `> 下界` 这类判断反向
通过；更阴的是存在某些取值回绕成"看起来合法的小正数"，于是脚本不报错、静默算出一个
**完全错误但仍自洽**的计划。这不是理论问题：`TARGET_MIB=99999999` 在 32 位下会算出负的
`NEW_SYS_END`，报出来的错是"不大于现有大小"，与真正原因（越盘）毫不相干。

**对策（两条都要）**：

1. 用户输入在**任何乘法之前**就卡死：必须纯数字，且位数设上限
   （`case "$TARGET_MIB" in ''|*[!0-9]*) die ...;; esac` + `[ "${#TARGET_MIB}" -le 6 ]`），
   再与上界比。上界要用**除法**算出：`MAX_MIB=$(( (last_usable - start + 1) / SPM ))`
   ——结果 ≤ 磁盘扇区数，远小于 2³¹。
2. 所有边界比较都在**小尺度**（MiB / 扇区，值 ≤ 磁盘扇区数）上做，**绝不做
   `扇区 × 512`** 这种可能越 2³¹ 的中间运算。要显示 MiB 就用 `$(( 扇区 / 2048 ))`，
   不要用 `$(( 扇区 * 512 / 1048576 ))`（后者在扇区 > 4,194,303 时就溢出：
   `TARGET_MIB=8192` 会把 6758 MiB 打印成 2663 MiB）。

对照探测（确认自己那台是不是 32 位）：

```bash
adb shell 'echo $((2147483647+1))'   # 32 位 -> -2147483648
```

> 另外：光有"磁盘末尾"检查还不够。要**逐项**校验计划里每个分区的 `ns..ne` 都落在
> `1..last_usable` 内。原因见 §4。

## 3. 先查 fstab 的 `formattable`

决定"被平移的分区能否自动重建"：

```bash
mkdir -p /mnt/v; mount -t ext4 -o ro /dev/block/mmcblk0p<vendor> /mnt/v
grep -E 'userdata|cache|system' /mnt/v/etc/fstab*; umount /mnt/v
```

典型输出：

```
.../by-name/userdata /data  ext4 ... quota,reservedsize=128M,formattable,resize,encryptable=...
.../by-name/cache    /cache ext4 ... wait,check,formattable
.../by-name/system   /      ext4 ro  wait,recoveryonly
```

`formattable` 在 → 挂不上就自动格式化，平移后不用手动 mke2fs。
（顺带：`resize` 让 init 把 fs 撑满分区。）

## 4. 计算新布局

设 `SPM=2048`（每 MiB 的 512B 扇区数），目标大小 `TARGET_MIB`：

```
SYS_START    = 原值（不动）
NEW_SYS_END  = SYS_START + TARGET_MIB*SPM - 1
DELTA        = NEW_SYS_END - 原 SYS_END

# 需要平移的分区 = 起点落在 (原 SYS_END, NEW_SYS_END] 的那一串（按起点排序）
cursor = NEW_SYS_END + 1
for m in 平移串:
    ns = cursor
    ne = ns + (m.end - m.start + 1) - 1
    if m 是串里最后一个: ne = m.end      # 右端保持不动，多退少补都落它身上
    cursor = ne + 1
assert cursor == (串里最后一个的原 end + 1)   # 否则说明算错了
```

对"system + 后面紧跟 vbmeta/cache/userdata"这种布局，效果就是：前三者原样平移，
userdata 缩小、右端不动。

**校验**（两道，不可互替）：
1. `NEW_SYS_END` 必须在 `1..last_usable` 内（`last_usable = 磁盘总扇区 - 34`，
   后面 34 扇区是备份 GPT）。
2. **计划里每一个分区**的 `ns..ne` 都必须在 `1..last_usable` 内。只查 ① 不够——
   当平移串里含 `otp`/`flashinfo` 这类尾部小分区时，中间项可能越界而 ① 看不出来
   （实例：目标设成 disk 上限的 14029 MiB，① 放行，② 抓出
   `#32 vbmeta 30533632..30563135` 越出 `30535646`）。

## 5. 用 sgdisk 一把写（就是 4 个动作，顺序固定）

```
sgdisk \
  --delete=31 --delete=32 --delete=33 --delete=34 \
  --new=31:<start>:<new_end> --typecode=31:0700 --change-name=31:system  --partition-guid=31:<原 GUID> \
  --new=32:... --typecode=32:0700 --change-name=32:vbmeta --partition-guid=32:<原 GUID> \
  ... \
  /dev/block/mmcblk0
```

- `--delete` + `--new` **必须写在同一条命令里**（sgdisk 内部先处理删除再处理新建），
  拆成两次会在中间留下一个残缺表。
- `--new` 会重置 type/name/GUID 为默认值 ⇒ **必须**补回 `--typecode` `--change-name`
  `--partition-guid`，否则按名找分区的 `by-name` 会失效。
- sgdisk 会一并重算 GPT 的 CRC32、主/备表、last-usable —— 这正是**不要手工拼 GPT 二进制**
  的理由。

**回滚**：`sgdisk --load-backup=<备份文件> <disk>` 可整表还原。

## 6. ★ 改表之后，kernel 里的分区表是旧的

`/dev/block/mmcblk0pNN` 这些节点还是**旧偏移/旧大小**。所以改表后的任何按 LBA 读写
**必须走裸盘 + seek**：

```bash
dd if=/tmp/x.bin of=/dev/block/mmcblk0 bs=512 seek=<新 LBA> conv=notrunc
```

用 `/dev/block/mmcblk0pNN` 会写到旧位置 → 静默损坏。改完 `sync` 然后 `reboot`，
让 kernel 重新读表。

## 7. 完整流程（推荐写成脚本）

1. 环境与工具自检；**不在 recovery 就拒绝**（Android 运行时 /data /cache 在用，
   在线改表会让内核继续往旧偏移写）。
2. **卸载**：`/sdcard`（先，它是 /data 的 bind）、`/data`、`/cache`、`/system`、`/vendor`。
   进 TWRP 时这几个通常是挂着的。
3. 解析 `sgdisk --print` → 预检连续性 → 算新布局 → **打印计划表**。
4. 默认 **dry-run**，要写盘才 `APPLY=1`。
5. 备份：`sgdisk --backup` + 裸备份头/尾各 34 扇区（`dd bs=512 count=34`、
   `dd bs=512 skip=$(disk_size-34) count=34`）+ 需要保留的分区内容。
6. 一把 sgdisk 改表。
7. `sgdisk --verify` + 重新 `--print` **逐项比对** start/end/name；不符立即 `--load-backup`。
8. 把备份的内容（如 vbmeta）写回新 LBA，读回 sha256 复核。
9. 把可丢弃分区（cache/userdata）新位置的开头清零（清掉旧超级块，配合 fstab 的
   `formattable` 让首启自动重建）。ext4 主超级块在 fs 起始 1 MiB 内，1 MiB 即够；
   清 4 MiB 留余量。**不必**清备份超级块——ext4 默认不自动回退到 `sb=`。
10. 打印后续命令：`reboot` + 用 `blockdev --getsize64 /dev/block/by-name/X` 复核。
    **期望值要按本次计划算出来打印，别硬编码**（改布局后硬编码必然漂移）。

## 8. 踩坑清单

| 现象 | 原因 / 解法 |
|---|---|
| **脚本跑到一半断了** | 脚本放 `/data/local/tmp/`，而它自己 `umount /data` 后 shell 读不到后续内容。**放 `/tmp` 跑** |
| `sgdisk: invalid option -- p` | TWRP 的裁剪版只认长选项，用 `--print` / `--info=N` |
| 改完表读的还是旧大小 | kernel 分区表是旧的。`reboot`，或改表后的读写一律 `dd ... seek=` 走裸盘 |
| 用 `mmcblk0pNN` 写数据损坏了 | 同上：改表后分区节点仍指向旧偏移 |
| `mount -o rw` 报 Invalid argument | `Same superblock, different security settings`。先 `umount` + `losetup -d` 再重挂 |
| TWRP 的 `dd` 报 `block size '4M': illegal number` | toybox dd 只吃纯数字，**且报错后静默不写**。写 `bs=4194304` |
| TWRP 的 `losetup -f` 报 `No such device or address` | toybox 的 `-f` 是坏的，显式指定 `/dev/block/loop0..7` |
| `e2fsck`/`resize2fs` 打不开 `/dev/block/loopN` | loop 上 O_DIRECT 对齐问题，改传**镜像文件路径** |
| Android 侧 `blockdev` 报 Permission denied | 需要先 `adb root` |
| MSYS 下 `adb push` 目的地变成 `C:/...` | `export MSYS_NO_PATHCONV=1`，此时**源路径要写 `D:/...`**（`/d/...` 反而找不到） |
| **守卫被绕过 / 报错信息与原因不符** | TWRP 的 `sh` 是 **32 位算术**（§2b）。输入必须先卡位数、比较一律在小尺度做 |
| 打印的 MiB 数是错的 | `$(( 扇区 * 512 / 1048576 ))` 越 2³¹ 溢出。用 `$(( 扇区 / 2048 ))` |

## 9. 验证"真的没写盘"

改表脚本跑完（或 dry-run 后）用设备自己核对，别信日志：

```bash
adb root && adb shell 'for p in system vendor cache userdata; do
  printf "%-9s %s B\n" "$p" "$(blockdev --getsize64 /dev/block/by-name/$p)"; done'
```

换算：`扇区数 × 512`。改表**前**如果值就是原值，说明确实没写。
