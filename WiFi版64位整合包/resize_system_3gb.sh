#!/sbin/sh
# ============================================================================
#  resize_system_3gb.sh
#  在 TWRP(recovery) 里把 system 分区扩容到 3 GiB（默认 3072 MiB）
#
#  ---------------------------------------------------------------------------
#  为什么不能只改 system 一项
#  ---------------------------------------------------------------------------
#  GPT 里 system(#31) 的 LBA 后面紧跟着 vbmeta(#32)、cache(#33)、userdata(#34)，
#  四者首尾相连、中间没有空隙。分区必须连续，所以 system 要变大，就必然要把
#  后面这三者整体向后平移 delta = (新大小 - 旧大小) 个扇区。
#  otp(#35) / flashinfo(#36) 在 userdata 之后，不受影响。
#
#  ---------------------------------------------------------------------------
#  平移的代价（脚本自动处理）
#  ---------------------------------------------------------------------------
#    vbmeta   —— 改表前把原内容备份出来，改表后写回它的新位置（内容不丢）
#    cache    —— 新位置开头清零；vendor fstab 里带 `formattable`，Android 首启自动重建
#    userdata —— 新位置开头清零；fstab 带 `formattable,resize`，首启自动重建
#                ★ 数据全部丢失，无法保留（分区起点都变了，旧文件系统不可能原地可用）
#                ★ 而且 **userdata 会缩小 delta**：它的右端钉死不动（这样 otp/flashinfo
#                  才不用动），起点右移 delta ⇒ 净减 delta。这是**唯一可行解**——
#                  磁盘尾部（flashinfo 之后）只剩 34 扇区，没有任何余量让整条链后移。
#    system   —— 起始 LBA 没变，**里面的 GSI 原样保留**，不会被重刷
#
#  结论：扩容 system 会清空 /data，并让 userdata 缩小 delta。这不是脚本的取舍，
#        是"分区必须连续 + 磁盘尾部没有余量"两个硬约束共同决定的结果。
#
#  ---------------------------------------------------------------------------
#  用法（必须让设备进入 TWRP 后再跑）
#  ---------------------------------------------------------------------------
#      sh resize_system_3gb.sh             # 只打印计划，不写任何东西（默认）
#      APPLY=1 sh resize_system_3gb.sh     # 真正执行
#
#  环境变量：
#      TARGET_MIB=3072    目标大小(MiB)，默认 3072 = 3 GiB
#      DISK=/dev/block/mmcblk0
#      SYS_NAME=system
#      APPLY=0|1          是否真的写盘（默认 0 = 预演）
#      FORCE=0|1          不在 recovery 时也允许继续（危险，不建议）
#      WORK=/tmp          备份落盘目录。TWRP 的 /tmp 是 tmpfs，重启即失，
#                         需要留档请在重启前 `adb pull` 走。
#
#  设计要点：
#    · 全程只用 TWRP 自带的 sgdisk 改 GPT——CRC32、备份表、last-usable 它都会
#      一并处理，比手工拼 GPT 二进制安全得多。
#    · 改表前先 sgdisk --backup + 裸备份头/尾各 34 扇区；写表后逐项比对，
#      任何不符立即 --load-backup 回滚。
#    · 依赖工具在做任何事之前全部解析并**实测**：sgdisk/dd/awk/sha256sum。
#      sha256sum 不只看存在性，还拿 sha256("abc") 的已知向量验它算得对。
#    · 改表之后 kernel 里的分区表是**旧的**，所以后面所有按 LBA 的读写一律
#      走 `$DISK` + seek=，绝不用 /dev/block/mmcblk0pNN（否则会写到旧偏移）。
#    · ★ TWRP 的 /sbin/sh 是 **32 位算术**（实测 $((2147483647+1)) = -2147483648，
#      $((99999999*2048)) = -1358432256）。所以所有可能溢出的乘法都被刻意绕开：
#      TARGET_MIB 在任何乘法之前先卡死位数与上限；边界比较一律在 MiB 尺度做，
#      被比较的中间值都 ≤ 磁盘扇区数，远离 2^31。
# ============================================================================
set -u

DISK=${DISK:-/dev/block/mmcblk0}
TARGET_MIB=${TARGET_MIB:-3072}
SYS_NAME=${SYS_NAME:-system}
APPLY=${APPLY:-0}
FORCE=${FORCE:-0}
WORK=${WORK:-/tmp}

SEC=512                        # 逻辑扇区大小
SPM=2048                       # 每 MiB 的 512B 扇区数

die() { echo "!! $*" >&2; exit 1; }
hdr() { echo; echo "=== $* ==="; }
mib() { echo "$(( $1 / SPM ))"; }

# ★ TARGET_MIB 必须在**任何乘法之前**卡死。本机 TWRP 的 /sbin/sh 是 32 位算术
#   （已实测：$((2147483647+1)) = -2147483648，$((99999999*2048)) = -1358432256），
#   TARGET_MIB*2048 一旦越过 2^31 就回绕成负数/小正数，能把后面所有 $(( )) 边界
#   检查骗过去。所以这里先做纯字符串/小整数校验，把输入限制在安全区。
case "$TARGET_MIB" in ''|*[!0-9]*) die "TARGET_MIB 必须是正整数（当前 '$TARGET_MIB'）";; esac
[ "$TARGET_MIB" -ge 1 ] || die "TARGET_MIB 必须 ≥ 1"
[ "${#TARGET_MIB}" -le 6 ] || die "TARGET_MIB 数值过大（'$TARGET_MIB'，最多 6 位）"

# ---------------------------------------------------------------- 0. 工具/环境
SGDISK=$(command -v sgdisk 2>/dev/null || true)
[ -n "$SGDISK" ] || SGDISK=/sbin/sgdisk
[ -x "$SGDISK" ] || die "找不到 sgdisk（本脚本依赖 TWRP 自带 sgdisk 处理 GPT 的 CRC 与备份表）"
DD=$(command -v dd 2>/dev/null || true)
[ -n "$DD" ] || die "找不到 dd"
command -v awk >/dev/null 2>&1 || die "找不到 awk"

# ★ sha256sum：vbmeta 写回校验要用。实测本机 TWRP 3.5.2_9-0 有 /sbin/sha256sum
#   （toybox 的符号链接，PATH=/sbin:/system/bin 可直接命中）。但别的 TWRP 构建
#   可能只提供 `toybox sha256sum` 子命令，也可能二进制在但被裁坏——所以不能只
#   `command -v`，还要**实算一个已知向量**确认它真的算得对：存在但算错比不存在
#   更危险（会拿一个错误哈希去比对，反而误判为"写回失败"或更糟）。
#   探测顺序：bare 名 → 绝对路径 → toybox 子命令。
SHA_VEC=ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad   # sha256("abc")
SHA256=""
for c in sha256sum /sbin/sha256sum /system/bin/sha256sum; do
  if command -v "$c" >/dev/null 2>&1 && [ "$(printf abc | "$c" 2>/dev/null | awk '{print $1}')" = "$SHA_VEC" ]; then
    SHA256="$c"; break
  fi
done
if [ -z "$SHA256" ] && [ "$(printf abc | toybox sha256sum 2>/dev/null | awk '{print $1}')" = "$SHA_VEC" ]; then
  SHA256="toybox sha256sum"          # 故意留空格：下面按 $SHA256 展开做词分割
fi
[ -n "$SHA256" ] \
  || die "找不到可用的 sha256sum（vbmeta 写回校验依赖它）。已探测 sha256sum / /sbin / /system/bin / toybox 子命令，均不可用或算错。"
[ -b "$DISK" ] || die "$DISK 不是块设备"
[ "$(id -u)" = 0 ] || die "需要 root（TWRP 的 adb shell 本来就是 root）"

if [ -z "$(getprop ro.twrp.version 2>/dev/null)" ] && [ "$FORCE" != 1 ]; then
  die "当前不在 recovery(TWRP) 中。改分区表必须在 recovery 里做——正常运行的系统里 /data、/cache 正在被使用，
       在挂载状态下改表会让内核继续往旧偏移写，直接损坏数据。确实要强行继续请加 FORCE=1"
fi
mkdir -p "$WORK" || die "无法创建 $WORK"

hdr "0. 环境"
echo "  disk      : $DISK"
echo "  sgdisk    : $SGDISK"
echo "  dd        : $DD"
echo "  sha256sum : $SHA256"
echo "  recovery  : $(getprop ro.twrp.version 2>/dev/null)"
echo "  目标大小  : ${TARGET_MIB} MiB ($(( TARGET_MIB / 1024 )) GiB = $(( TARGET_MIB * SPM )) 扇区)"
if [ "$APPLY" = 1 ]; then echo "  模式      : ★ 执行（会写盘）"; else echo "  模式      : 预演（只打印计划）"; fi

# ------------------------------------------------------------------ 1. 卸载
hdr "1. 卸载相关分区"
for m in /sdcard /data /cache /system /vendor /mnt/v; do
  if mount | grep -q " $m "; then
    umount "$m" 2>/dev/null && echo "  已卸载 $m" || echo "  !! umount $m 失败"
  fi
done
echo "  已按挂载点尝试卸载。要动的分区号得读完分区表才知道，稍后按**设备节点**再做一次权威校验。"

# ------------------------------------------------------------ 2. 读当前分区表
hdr "2. 当前分区表"
"$SGDISK" --print "$DISK" > "$WORK/gpt_before.txt" 2>/dev/null || die "sgdisk --print 失败"
sed -n '/^Number/,$p' "$WORK/gpt_before.txt" | sed 's/^/  /'
# 归一化：num start end code name（Size 列里带空格，所以取倒数两列）
awk '/^Number/{t=1;next} t && $1 ~ /^[0-9]+$/ { printf "%s %s %s %s %s\n", $1, $2, $3, $(NF-1), $NF }' \
    "$WORK/gpt_before.txt" > "$WORK/tbl.txt"
[ -s "$WORK/tbl.txt" ] || die "解析分区表失败"

SYS_ROW=$(awk -v n="$SYS_NAME" '$5 == n' "$WORK/tbl.txt")
[ -n "$SYS_ROW" ] || die "分区表里没有名为 $SYS_NAME 的分区"
SYS_NUM=$(echo "$SYS_ROW"   | awk '{print $1}')
SYS_START=$(echo "$SYS_ROW" | awk '{print $2}')
SYS_END=$(echo "$SYS_ROW"   | awk '{print $3}')
SYS_CODE=$(echo "$SYS_ROW"  | awk '{print $4}')
SYS_GUID=$("$SGDISK" --info="$SYS_NUM" "$DISK" 2>/dev/null | awk '/Partition unique GUID/{print $4}')

# 磁盘总扇区数（512B 逻辑扇区）。备份 GPT 占磁盘最后 34 扇区
# （entries 32 扇区 + header 1 扇区，末尾还留 1），故最后可用 LBA = DSZ - 34。
DSZ=$(cat "/sys/class/block/$(basename "$DISK")/size" 2>/dev/null)
case "$DSZ" in ''|*[!0-9]*) die "读不到磁盘扇区数（/sys/class/block/$(basename "$DISK")/size）";; esac
LAST_USABLE=$(( DSZ - 34 ))

# ★ 先在 MiB 尺度比较，再算扇区：/sbin/sh 是 32 位算术（见上文），
#   而 (LAST_USABLE - SYS_START + 1) 与它除以 SPM 的结果都 ≤ 磁盘扇区数，
#   远小于 2^31，绝不会溢出；TARGET_MIB*2048 却会。所以顺序很关键。
MAX_MIB=$(( (LAST_USABLE - SYS_START + 1) / SPM ))
CUR_MIB=$(mib $(( SYS_END - SYS_START + 1 )))
[ "$TARGET_MIB" -le "$MAX_MIB" ] \
  || die "目标 ${TARGET_MIB} MiB 超出磁盘可用范围：从 $SYS_NAME 起始 LBA $SYS_START 算，最大只能到 $MAX_MIB MiB。"
[ "$TARGET_MIB" -gt "$CUR_MIB" ] \
  || die "目标 ${TARGET_MIB} MiB 不大于现有 $CUR_MIB MiB——本脚本只扩容不缩容。"

NEW_SYS_END=$(( SYS_START + TARGET_MIB * SPM - 1 ))
[ "$NEW_SYS_END" -le "$LAST_USABLE" ] || die "内部不一致：新尾 $NEW_SYS_END > 最后可用 $LAST_USABLE"
DELTA=$(( NEW_SYS_END - SYS_END ))

# ------------------------------------------------- 3. 找出要平移的分区并校验连续性
# 要平移的是：起点落在 (system 旧尾, system 新尾] 区间内的分区
awk -v a="$SYS_END" -v b="$NEW_SYS_END" '$2 > a && $2 <= b' "$WORK/tbl.txt" \
  | sort -k2 -n > "$WORK/movers.txt"
[ -s "$WORK/movers.txt" ] || die "system 之后没有任何分区需要让路？分区表异常，拒绝继续"

FIRST_MOVER=$(head -n1 "$WORK/movers.txt" | awk '{print $2}')
[ "$FIRST_MOVER" = "$(( SYS_END + 1 ))" ] \
  || die "#$SYS_NAME 结束于 $SYS_END，紧随其后的分区却从 $FIRST_MOVER 开始（不连续），拒绝继续"

PREV=""
while read -r n s e c name; do
  [ -n "$PREV" ] && [ "$s" != "$(( PREV + 1 ))" ] \
    && die "平移链在 #$n 处断开（期望起点 $(( PREV + 1 ))，实际 $s），拒绝继续"
  PREV=$e
done < "$WORK/movers.txt"
LAST_MOVER_END=$PREV

# 现在知道要动哪些分区了（system + 平移链）。按**设备节点**做一次权威挂载校验，
# 不再依赖硬编码的分区号正则（那玩意在分区号不同的机器上会静默漏检）。
TOUCH_NUMS="$SYS_NUM"
while read -r n s e c name; do TOUCH_NUMS="$TOUCH_NUMS $n"; done < "$WORK/movers.txt"
for n in $TOUCH_NUMS; do
  d="$DISK$n"
  mount | grep -qE "(^|[[:space:]])$d " && umount "$d" 2>/dev/null
done
sync
for n in $TOUCH_NUMS; do
  d="$DISK$n"
  if mount | grep -qE "(^|[[:space:]])$d "; then
    mount | grep -E "$d "
    die "分区 #$n ($d) 仍被挂载，拒绝继续（在挂载状态下改表会让内核继续写旧偏移）"
  fi
done
if mount | grep -qE "by-name/(system|vendor|cache|userdata)"; then
  mount | grep -E "by-name/(system|vendor|cache|userdata)"
  die "有目标分区经 by-name 仍被挂载，拒绝继续"
fi
echo "  ok：要动的分区（#$TOUCH_NUMS）均已卸载"

# ------------------------------------------------------------- 4. 计算新布局
P="$WORK/plan.txt"; : > "$P"
echo "$SYS_NUM $SYS_START $SYS_END $SYS_START $NEW_SYS_END $SYS_CODE $SYS_NAME $SYS_GUID" >> "$P"

CURSOR=$(( NEW_SYS_END + 1 ))
TOTAL=$(grep -c . "$WORK/movers.txt")
i=0
while read -r n s e c name; do
  i=$(( i + 1 ))
  size=$(( e - s + 1 ))
  ns=$CURSOR
  ne=$(( ns + size - 1 ))
  [ "$i" = "$TOTAL" ] && ne=$e          # 链尾右端保持不动，多退少补都落在这里
  g=$("$SGDISK" --info="$n" "$DISK" 2>/dev/null | awk '/Partition unique GUID/{print $4}')
  echo "$n $s $e $ns $ne $c $name $g" >> "$P"
  CURSOR=$(( ne + 1 ))
done < "$WORK/movers.txt"

[ "$CURSOR" = "$(( LAST_MOVER_END + 1 ))" ] \
  || die "内部校验失败：平移链尾部未对齐（cursor=$CURSOR，期望 $(( LAST_MOVER_END + 1 )))"

# 逐项边界校验：每个分区都必须落在 [1, LAST_USABLE] 且 ns <= ne。
# 这条能拦住 TARGET_MIB 过大导致链上某个分区被推到磁盘外（含 GPT 备份表）的情况。
while read -r n os oe ns ne c name g; do
  [ "$ns" -ge 1 ] && [ "$ne" -le "$LAST_USABLE" ] && [ "$ns" -le "$ne" ] \
    || die "计划越界：#$n ($name) 新区间 $ns..$ne 不在 1..$LAST_USABLE 内——多半是 TARGET_MIB 过大"
done < "$P"

# --------------------------------------------------------------- 5. 打印计划
hdr "3. 变更计划"
printf '  %-4s %-10s %11s %11s   %s\n' "#" "name" "old(MiB)" "new(MiB)" "new LBA start..end"
while read -r n os oe ns ne c name g; do
  printf '  #%-3s %-10s %11s %11s   %s..%s\n' \
      "$n" "$name" "$(mib $(( oe - os + 1 )))" "$(mib $(( ne - ns + 1 )))" "$ns" "$ne"
done < "$P"
echo
echo "  system  : $(mib $(( SYS_END - SYS_START + 1 ))) MiB -> ${TARGET_MIB} MiB  (+$(( DELTA / SPM )) MiB)"
echo "  平移量  : delta = $DELTA 扇区 ($(( DELTA / SPM )) MiB)"
# 明确把 userdata 的缩容摆出来。磁盘尾部（flashinfo 之后）只有 34 扇区，没有余量，
# 所以扩容占用的空间**只能**由 userdata 让出——这不是取舍，是唯一可行解。
awk -v spm="$SPM" '$7 == "userdata" {
  printf "  userdata: %d MiB -> %d MiB  (-%d MiB，磁盘尾部无余量，扩容份额只能由它让出)\n",
         ($3-$2+1)/spm, ($5-$4+1)/spm, (($3-$2+1)-($5-$4+1))/spm }' "$P"
echo
echo "  ★ cache 与 userdata 会被清空并由 Android 首启自动重建（/data 数据将丢失）"
echo "  ★ system 起始 LBA 不变，现有系统内容保留"

if [ "$APPLY" != 1 ]; then
  echo
  echo "==> 预演结束，未做任何修改。"
  echo "    确认无误后执行：  APPLY=1 sh $0"
  exit 0
fi

# ------------------------------------------------------------------ 6. 备份
hdr "4. 备份"
TS=$(date +%Y%m%d-%H%M%S 2>/dev/null); [ -n "$TS" ] || TS=$$
BK="$WORK/gpt_backup_$TS.bin"
"$SGDISK" --backup="$BK" "$DISK" >/dev/null 2>&1 && echo "  GPT 全量备份 -> $BK" \
  || die "GPT 备份失败，中止（未做任何修改）"
$DD if="$DISK" of="$WORK/gpt_head_$TS.bin" bs=$SEC count=34                        2>/dev/null
$DD if="$DISK" of="$WORK/gpt_tail_$TS.bin" bs=$SEC skip=$(( DSZ - 34 )) count=34   2>/dev/null
echo "  裸 GPT 头/尾 -> $WORK/gpt_head_$TS.bin  $WORK/gpt_tail_$TS.bin"

VB_NS=""; VB_SZ=""
VB_LINE=$(awk '$7 == "vbmeta"' "$P")
if [ -n "$VB_LINE" ]; then
  VB_OS=$(echo "$VB_LINE" | awk '{print $2}')
  VB_SZ=$(echo "$VB_LINE" | awk '{print $3 - $2 + 1}')
  VB_NS=$(echo "$VB_LINE" | awk '{print $4}')
  $DD if="$DISK" of="$WORK/vbmeta_$TS.bin" bs=$SEC skip=$VB_OS count=$VB_SZ 2>/dev/null
  echo "  vbmeta 内容 -> $WORK/vbmeta_$TS.bin ($VB_SZ 扇区)"
else
  echo "  （计划里没有 vbmeta，跳过其内容备份）"
fi

# -------------------------------------------------------------- 7. 写新分区表
hdr "5. 写入新分区表 (sgdisk)"
# 用 POSIX 的 `set -- "$@" ...` 积累 argv，而不是拼一个字符串再 `set -- $ARGS`——
# 后者会做 word splitting，且如果分区名/GUID 里出现空白、`*` 之类会被切坏或 glob。
set --
while read -r n os oe ns ne c name g; do set -- "$@" "--delete=$n"; done < "$P"
while read -r n os oe ns ne c name g; do
  set -- "$@" "--new=$n:$ns:$ne" "--typecode=$n:$c" "--change-name=$n:$name"
  [ -n "$g" ] && set -- "$@" "--partition-guid=$n:$g"
done < "$P"
echo "  sgdisk $*  $DISK"

if ! "$SGDISK" "$@" "$DISK"; then
  echo "  !! sgdisk 写入失败，从备份回滚……"
  if "$SGDISK" --load-backup="$BK" "$DISK"; then echo "  已回滚到原分区表"; else echo "  !! 回滚亦失败，请用 $BK 手工恢复"; fi
  die "写新分区表失败"
fi
sync

# ------------------------------------------------------------------ 8. 校验
hdr "6. 校验"
"$SGDISK" --verify "$DISK" 2>&1
"$SGDISK" --print "$DISK" > "$WORK/gpt_after.txt" 2>/dev/null
sed -n '/^Number/,$p' "$WORK/gpt_after.txt" | sed 's/^/  /'
awk '/^Number/{t=1;next} t && $1 ~ /^[0-9]+$/ { printf "%s %s %s %s %s\n", $1, $2, $3, $(NF-1), $NF }' \
    "$WORK/gpt_after.txt" > "$WORK/tbl_after.txt"
FAIL=""
while read -r n os oe ns ne c name g; do
  got=$(awk -v k="$n" '$1 == k' "$WORK/tbl_after.txt")
  gs=$(echo "$got" | awk '{print $2}'); ge=$(echo "$got" | awk '{print $3}'); gn=$(echo "$got" | awk '{print $5}')
  [ "$gs" = "$ns" ] && [ "$ge" = "$ne" ] && [ "$gn" = "$name" ] || FAIL="$FAIL #$n"
done < "$P"
if [ -n "$FAIL" ]; then
  echo "  !! 校验不符：$FAIL —— 回滚"
  "$SGDISK" --load-backup="$BK" "$DISK" && echo "  已回滚" || echo "  !! 回滚失败，用 $BK 手工恢复"
  die "新分区表校验未通过"
fi
echo "  ok：新表与计划逐项一致"

# --------------------------------------------------------- 9. 恢复 vbmeta 内容
if [ -n "$VB_NS" ]; then
  hdr "7. 把 vbmeta 内容写回新位置"
  $DD if="$WORK/vbmeta_$TS.bin" of="$DISK" bs=$SEC seek="$VB_NS" conv=notrunc 2>/dev/null
  sync
  H1=$($SHA256 "$WORK/vbmeta_$TS.bin" | awk '{print $1}')
  H2=$($DD if="$DISK" bs=$SEC skip="$VB_NS" count="$VB_SZ" 2>/dev/null | $SHA256 | awk '{print $1}')
  [ "$H1" = "$H2" ] && echo "  vbmeta 已写回 @$VB_NS（sha256 $H1 一致 ✓）" \
                    || die "vbmeta 写回校验失败（$H1 != $H2）"
fi

# --------------------------------------------- 10. 作废 cache / userdata 旧内容
hdr "8. 作废 cache / userdata 的旧内容（首启自动重建）"
while read -r n os oe ns ne c name g; do
  case "$name" in
    cache|userdata)
      # 只要破坏 fs 的**主**超级块（位于 fs 起始 1 MiB 内）就能让首启的 mount 失败，
      # 从而触发 fstab 的 formattable 重建。这里清 4 MiB 留足余量；备份超级块在
      # fs 起始 128 MiB 之后，但 ext4 默认**不会**自动回退到备份 sb（那要显式 sb=），
      # 所以无需清。反正这两块数据本来就要丢。
      echo "  清零 $name 新位置开头 @$ns"
      $DD if=/dev/zero of="$DISK" bs=$SEC seek="$ns" count=8192 conv=notrunc 2>/dev/null
      ;;
  esac
done < "$P"
sync

# ------------------------------------------------------------------ 11. 收尾
hdr "9. 完成"
echo "  新分区表已写入并校验通过。"
echo
echo "  备份（TWRP 的 $WORK 是 tmpfs，重启即失，需要留档现在 pull 走）："
echo "    adb pull $BK"
echo "    adb pull $WORK/gpt_head_$TS.bin"
echo "    adb pull $WORK/gpt_tail_$TS.bin"
[ -n "$VB_NS" ] && echo "    adb pull $WORK/vbmeta_$TS.bin"
echo
SYS_EXP=$(awk -v nm="$SYS_NAME" '$7 == nm { print ($5-$4+1)*512 }' "$P")
UD_EXP=$(awk '$7 == "userdata" { print ($5-$4+1)*512 }' "$P")
echo "  下一步："
echo "    1) reboot                     # 重启进 Android（首启会重建 cache/userdata，稍慢）"
echo "    2) 校验新容量（期望值由本次计划算出，不是硬编码）："
echo "         adb shell blockdev --getsize64 /dev/block/by-name/$SYS_NAME   # 期望 $SYS_EXP"
[ -n "$UD_EXP" ] && echo "         adb shell blockdev --getsize64 /dev/block/by-name/userdata # 期望 $UD_EXP"
echo
echo "  说明：system 里现有的 ext4 仍是 1.4 GiB。多出来的空间是给"
echo "        \"刷更大的镜像\"用的；要让现有系统占满 3 GiB 需要在非挂载状态下"
echo "        resize2fs，而 system 是根文件系统、无法在线扩容。"
