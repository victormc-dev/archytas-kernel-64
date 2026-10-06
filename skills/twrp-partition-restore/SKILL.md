---
name: twrp-partition-restore
description: 设备能进 TWRP（recovery）时，用 adb + dd 直接读写裸分区来刷机/回退/救砖——不需要 mtkclient、不需要 fastboot、不需要 BROM。本技能记录关键坑：**TWRP 的 toybox dd 不认 `bs=4M` 这类后缀，会静默什么都不写**；`/tmp` 是 tmpfs 只有 ~1GB 放不下大镜像要借 `/data`；`losetup -f` 坏的；以及"push 后 + 从分区读回"双重 sha256 校验流程。Trigger words: TWRP 刷机, recovery 刷分区, dd 写分区, 回退刷机, 救砖, toybox dd illegal number, bs=4M, adb push 到 data, 从分区读回校验, by-name, 抹超级块.
agent_created: true
---

# TWRP 里用 dd 直接刷裸分区

## 何时用

- 设备**能进 TWRP**（`adb devices` 显示 `recovery`），要刷 / 回退 / 修复某个分区。
- 比 mtkclient（要 BROM + 驱动 + 可能还要 auth 密码）和 fastboot（要有可用的 bootloader
  且分区要能被 fastboot 识别）**都省事**：TWRP 的 adbd 本来就是 root。

判断设备确实在 TWRP：

```bash
adb devices -l          # -> <serial>  recovery  product:omni_XXX model:XXX
adb shell id            # -> uid=0(root) ... context=u:r:su:s0
```

## 核心事实

1. **TWRP 的 adbd 是 root**（`u:r:su:s0`），可以直接 `dd` 写 `/dev/block/by-name/<part>`。
2. **`/tmp` 是 tmpfs**，一般 ≈ 内存的一半（1.9 GB 内存 → 994 MiB）。放不下 1.4 GB 的
   system 镜像。⇒ 借 **`/data`** 当落脚点（先 `mke2fs`，反正刷完要清数据）。
3. **不要用 `adb shell "dd ..." < local.img` 想流式写入**：`adb shell` 会走 pty，
   二进制被 CR/LF 转换污染，镜像必坏。老老实实 push 成文件再 dd。
4. `userdata` 被 erase 过 / 从来没有 fs 时，`mount -t ext4` 会报 **`Invalid argument`**——
   这不是权限问题，是分区里没有文件系统。先 `mke2fs`。

## 标准流程

```bash
ADB="path/to/adb.exe"; export MSYS_NO_PATHCONV=1     # Git-Bash 必须禁路径转换
STAGE=/mnt/data                                       # 落脚点

# 0) 确认在 TWRP 且是 root
"$ADB" shell "id"
"$ADB" shell "ls -l /dev/block/by-name/"              # 拿分区名 -> 设备节点

# 1) 准备落脚点：格式化 + 挂载 data（12 GiB）
"$ADB" shell "umount $STAGE 2>/dev/null; \
  mke2fs -F -t ext4 -b 4096 /dev/block/by-name/userdata >/dev/null 2>&1; \
  mkdir -p $STAGE; mount -t ext4 -o rw /dev/block/by-name/userdata $STAGE; \
  df -h $STAGE"

# 2) push 镜像，push 完立刻比 sha256（push 会静默出错）
"$ADB" push "D:/path/system.img" $STAGE/system.img
sha256sum "D:/path/system.img" | cut -d' ' -f1                    # 本地
"$ADB" shell "sha256sum $STAGE/system.img" | cut -d' ' -f1        # 设备 —— 必须一致

# 3) 写分区。★ bs 必须写纯字节数，见下面大坑
"$ADB" shell "dd if=$STAGE/system.img of=/dev/block/by-name/system bs=4194304; sync"

# 4) ★ 从分区读回再比 sha256 —— 这才是"真写进去了"的唯一证据
BYTES=$(stat -c%s "D:/path/system.img"); BLKS=$((BYTES/4096))
"$ADB" shell "dd if=/dev/block/by-name/system bs=4096 count=$BLKS 2>/dev/null | sha256sum"

# 5) 清数据：抹掉超级块（等同 fastboot erase；分区没有 fs 时 Android 会自己重建）
"$ADB" shell "umount $STAGE 2>/dev/null; \
  for p in userdata metadata cache; do dd if=/dev/zero of=/dev/block/by-name/\$p bs=1048576 count=1; done; \
  sync"

# 6) 重启
"$ADB" reboot
```

## ★★ 大坑：toybox dd 不认 `bs=4M`

```
dd: block size '4M': illegal number
```

TWRP 里的 `dd` 是 **toybox**，`bs` 只接受纯数字，**不接受 `K`/`M`/`G` 后缀**（写 `4M`
直接报上面那句）。然后：

- **它什么都不写就退出了**（exit 不一定非 0，看版本）；
- 分区里还是**旧内容**；
- 于是随后的"从分区读回比对"也**不会报错**，只是 hash 对不上 ——

极易被误判成"写入失败/镜像坏了"，其实是**命令根本没执行**。

```bash
bs=4M          # ✗ toybox: illegal number
bs=4194304     # ✓ 4 MiB
bs=1048576     # ✓ 1 MiB
```

> 排查提示：如果 `dd` 的日志里**没有** `N+M records out` 这一行，就是没执行。
> 有那行才是真写了。

同源的另一个坑：**TWRP 的 `losetup -f` 也是坏的**（toybox），要用必须显式指定
`/dev/block/loop0..7`。

## TWRP 的脚本环境（3.5.2_9-0 实测）

`PATH=/sbin:/system/bin`，工具全在 `/sbin`（**没有 busybox**）：

| 工具 | 路径 | 备注 |
|---|---|---|
| sh | `/sbin/sh` | toybox。**32 位算术**，见 `mtk-gpt-repartition` 技能 |
| sgdisk | `/sbin/sgdisk` | 裁剪版，**只认长选项**（`--print` ✓ / `-p` ✗） |
| dd | `/sbin/dd` | toybox，`bs` 不收 `4M` 后缀 |
| awk | `/sbin/awk` | 可用 |
| sha256sum | `/sbin/sha256sum` | toybox 符号链接，**存在且正确** |

★ **heredoc 在 TWRP 的 sh 里会失败**：

```sh
cat > /tmp/x <<'EOF'      # ✗ can't create temporary file : No such file or directory
#!/sbin/sh
echo hi
EOF

printf '#!/sbin/sh\necho hi\n' > /tmp/x   # ✓ 用 printf
```

报错信息里的行号会指向 heredoc **之后**的行，极易误判。生成测试桩/脚本时一律用
`printf`。

★ **别只查工具存在性，要实测它算得对**。探测模式：

```sh
SHA_VEC=ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad  # sha256("abc")
SHA256=""
for c in sha256sum /sbin/sha256sum /system/bin/sha256sum; do
  command -v "$c" >/dev/null 2>&1 \
    && [ "$(printf abc | "$c" 2>/dev/null | awk '{print $1}')" = "$SHA_VEC" ] \
    && { SHA256="$c"; break; }
done
[ -n "$SHA256" ] || SHA256="toybox sha256sum"   # 最后的兜底入口
```

理由：一个**在但算错**的 `sha256sum` 返回 0 且给出垃圾哈希，比"不存在"更危险 ——
校验段落拿它去比对会误判。同理，任何"拿哈希做判据"的脚本都该先跑一遍已知向量。

## 校验才是重点

写裸分区**没有任何校验和**，而且刷错的后果是开不了机。所以两道校验缺一不可：

| 阶段 | 命令 | 防的是 |
|---|---|---|
| push 后 | 设备侧 `sha256sum <file>` vs 本地 | adb 传输损坏 |
| dd 后 | `dd if=<分区> bs=4096 count=<镜像字节/4096> \| sha256sum` vs 本地 | 写入失败 / 写错分区 |

`count` 用**镜像大小**算，别用分区大小 —— 分区通常比镜像大（系统分区都会有几十 MB 余量），
多读的尾部是上一次刷机的残留，hash 必然对不上。

## 多平台注意

- **Git-Bash (MSYS)**：`export MSYS_NO_PATHCONV=1`，否则 `/mnt/data` 被改写成
  `C:/.../mnt/data`；但禁用后**源路径必须写 `D:/...`**，写 `/d/...` 反而找不到。
- 找不到机器 `losetup -f` 时（见上），不要让脚本依赖它。
- 写 .gitignore / 脚本时注意：**gitignore 不支持行尾注释**（`dir/  # 说明` 整行会被
  当成模式，静默不生效）。

## 实测记录（Archytas / 小爱老师 Wi-Fi 版）

设备：MT6761，单 system 分区（`mmcblk0p31` = 1,503,232,000 B），TWRP 3.5.2。
把 system 换成 LineageOS 17.1 GSI（Android 10）后起不来 → 用本流程回退：

- 三分区（boot 32 MiB / vendor 400 MiB / system 1.4 GiB）push + dd，
  dd 速度约 **40 MB/s**（system 36.8 s）；
- 读回哈希与本地镜像**逐字节一致**；
- 抹超级块后 `reboot`，**30 秒**进系统（`sys.boot_completed=1`）；
- 复测：`arm64-v8a` / `zygote64_32` / Wi-Fi 可开关 / `avc denied` = 0。

现成脚本：`Archytas_64/WiFi版64位整合包/restore_via_twrp.sh`（含 SKIP_WIPE /
NO_VERIFY / NO_MOUNT / DEVICE 开关与分区容量预检）。
