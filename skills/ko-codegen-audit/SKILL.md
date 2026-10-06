---
name: ko-codegen-audit
description: 内核模块（.ko）"编译器误编"审计与门禁。当驱动出现**无出口死循环、进程满核空转、svc/ioctl 永久阻塞、insmod 报 unsupported RELA relocation**等"看着像逻辑 bug、其实是编译配置/误编"的症状时使用：读 DW_AT_producer 核对实际 codegen 开关、统计 ADRP 等重定位类型、用 Capstone 反汇编**已 strip 的出货字节**、与已知良好的参考构建逐指令对比、最后把判据做成构建门禁防回归。Trigger words: 误编, miscompile, 死循环, infinite loop, main_thread 空转, 卡死, .ko 反汇编, disassemble ko, DW_AT_producer, ADRP relocation, unsupported RELA relocation, 275, codegen 审计, 构建门禁, GCC 优化差异, 内核模块调试.
---

# 内核模块"编译器误编"审计

## Overview

内核/驱动里的"死锁""满核空转""永久阻塞"，先别急着读源码找逻辑 bug。
**先把出货的机器码读出来**——如果是编译器把正确的源码编错了，读源码永远找不到问题。
本技能给出一套可复现的判定流程：把"实际用了哪些 codegen 开关"和"实际发出了什么指令"都变成硬证据，
再与一个**已知良好的参考构建**对照，最后把判据固化成构建门禁。

适用信号（任一命中就该走本流程）：

- 进程 `state=R` 且 `stime` 以约 100 ticks/s 满核增长、`nvcsw=nivcsw=0`（从不睡眠）
- sysrq NMI 回栈（`echo l > /proc/sysrq-trigger`）多次落在**同一个 PC** 偏移上
- `ioctl`/命令等待固定超时（如 `wait main_thread timeout, duration:30720ms`）成串出现
- `insmod` 报 `unsupported RELA relocation: 275/276`、`Exec format error`
- 同一份源码在**另一个工具链**上正常、在我们这里不正常

## 第 0 步：先分清"逻辑 bug"还是"误编"

**不要靠读源码下结论。** 判据只有一条：**回边（back-edge）有没有出口**。

- 正确循环：回边是**条件**跳转（`b.ne` / `cbz` / `tbz`），且循环体内有让条件变化的**重载**
- 误编：回边是**无条件** `b`，且循环体内**缺少那条重载**（被优化器错误地提升到循环外，即非法 LICM）

## 第 1 步：拿到"实际生效"的 codegen 开关

`DW_AT_producer` 是唯一可信来源（`.o.cmd` 只能证明命令行，不能证明 cc1 真收到了）。

```bash
python -c "
import re,sys
d=open(sys.argv[1],'rb').read()
for m in re.finditer(rb'GNU C[0-9][^\x00]{0,240}', d): print(m.group(0).decode('latin1'))
" path/to/module.ko
```

注意：**`--strip-debug` 会抹掉它**（`strip -g` / `INSTALL_MOD_STRIP=1`）。
所以要么在 strip **之前**采集，要么直接看 CI 日志里提前打印的那一行。

## 第 2 步：用重定位分布做"代码模型体检"（比反汇编快得多）

ADRP 重定位（`R_AARCH64_ADR_PREL_PG_HI21/LO21` = 275/276）的出现方式，
直接反映符号寻址是走 **PC-relative 字面量池** 还是 **ADRP + GOT 间接**。

```python
import sys; sys.path.insert(0, 'port')
from collections import Counter
import audit_wmt_fb_workaround as A          # 或自写 ELF64 解析
e = A.ElfRel(sys.argv[1]); c = Counter()
for s in e.secs:
    for off, name, t, ad in e.relas_for(s['idx']): c[t] += 1
print({k: c[k] for k in (257, 258, 275, 276) if c.get(k)})
```

判读：**同一份源码、同一套源码开关下，若参考构建 ADRP=0 而我们上万，说明我们少了
`-mpc-relative-literal-loads`**（内核 `arch/arm64/Makefile` 通常用
`$(call cc-option, -mpc-relative-literal-loads)` 与 `-mcmodel=large` **成对**给出；
`cc-option` 静默失败时该开关会**悄悄消失**，落进内核从未测试过的组合——这正是误编的高发区）。

## 第 3 步：反汇编"出货字节"（必须用真反汇编器）

自写只解码分支的迷你解码器**不足以推理控制流**，用 Capstone：

```bash
python disasm_capstone.py <symbol> <module.ko> --lo 0x… --hi 0x…
```

读函数要盯的四件事：

1. 循环头在哪、回边是 `b` 还是 `b.<cond>`
2. 走链/迭代指针**每趟有没有被重载**（少了就是误编）
3. 有没有寄存器被无关 load 覆盖（例如计数器 load 覆盖了迭代指针）
4. 函数首尾与另一个构建差多少字节（差几条指令往往就是线索）

## 第 4 步：与"已知良好的参考构建"逐指令 diff

**这是最快定案的一步**：同一函数、两个构建、按 32-bit 字流对比
（AArch64 定长；**未重定位**的 `.ko` 里 `bl`=0x94000000、`adrp`=0x90000000，
所以可以直接比字流，不需要归一化地址）。

```bash
python diff_func_insns.py <symbol> ref.ko ours.ko
```

结论写法要落到"哪些指令在 A 有、在 B 没有"，例如：
"A 有 `ldr x1,[x25]` 重载 + `subs/b.ne` 条件回边；B 两样都没有"。

## 第 5 步：修复——两条腿，缺一不可

1. **对齐编译配置（治本）**：把缺失的开关显式加进 `KCFLAGS` **和**模块自己的 Makefile
   （`ccflags-y` / `subdir-ccflags-y`），别指望 `cc-option`。幂等标记 + 每次构建把
   实际开关打印出来。
2. **源码侧保险（治标但可证）**：在出问题的循环体末尾加编译器屏障
   ```c
   __asm__ __volatile__("" ::: "memory");
   ```
   内存 clobber 是**硬屏障**，任何编译器都不得把内存读跨越它 ⇒ 重载必然发生、循环必然可退出。
   它**不依赖代码模型**，即使开关将来回退也不会重新变成死循环。

## 第 6 步：把判据做成构建门禁（否则一定复发）

关键认知：**这类缺陷在符号检查、insmod、联网阶段完全不可见**
——模块能加载、能连、能跑流量，只在某个特定路径（如 drain 一个链表）时才卡死。
所以唯一能拦住它的是**读出货字节**的门禁：

```bash
python audit_scan_loop.py "$OUTDIR/module.ko" || { echo "ERROR: 循环无出口"; exit 1; }
```

门禁必须读**已 strip、已改名、即将推给设备**的那一份文件（不是中间 `.o`）。

## Pitfalls（都真实踩过）

- **`git push ... | tail -5; echo $?` 取的是 `tail` 的退出码**，会把失败当成功。
  推送后一定要用 `git ls-remote` 或分支 sha 复核。
- **交互式 credential helper 会让 `git push` 静默挂到超时**（如 WorkBuddy 的
  `helper-selector`）。绕法：`-c credential.helper=` 清空，再用带凭据的 URL（token 从
  项目里的 `ci_auth.py` 之类拿，别打印）。
- **别用"紧自环/无条件回边"当全模块嗅探器**：会被 `ASSERT()`/`BUG()`/`WARN` 的**共享尾块**
  大量污染（已知良好构建里也能刷出上千条假阳性）。要精确匹配"链表 unlink 惯用法"再判出口。
- **`.ko` 是 relocatable**：先确认符号所在 section 的 `sh_offset`，把 `st_value` 当 section 相对偏移，
  否则反汇编窗口会整体错位。
- ELF64 `r_info` 打包是 **`sym<<32 | type`**（与 ELF32 相反）；写反了符号名会全空。
- 结论要钉成"**哪条指令**在哪个地址"，不要写"疑似优化问题"。

## 交付物模板

一份可复核的结论至少包含：

1. 症状 + 现场证据（NMI 回栈 PC、`stime` 增速、超时字符串）
2. `DW_AT_producer` 两版对照（我们 vs 参考）
3. 重定位分布对照（ADRP 计数）
4. 同一函数的**两段反汇编并排**，标注缺失/多出的指令
5. 修复的三处改动 + 门禁脚本
6. 真机验收：修复前必挂的动作现在**返回耗时**与关键日志行
