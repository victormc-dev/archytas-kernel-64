#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""inject_impeller.py —— 往 APK 的 AndroidManifest.xml (AXML) 里注入两个 meta-data，
强制 Flutter 使用 Skia 后端（禁用 GL-Impeller）。

为什么需要：本机 GPU 是 PowerVR(IMG)，其 EGL 驱动不提供满足 Flutter GL-Impeller
要求的 offscreen(pbuffer) config；Flutter 在 API>=29 默认启用 Impeller，
Vulkan 又不可用 → 回退 GLES-Impeller → eglChooseConfig 返回 0 →
libflutter 未判空 → raster 线程 SIGSEGV。Android 9(API 28) 走 Skia 所以正常。

注入内容（application 节点下）：
    <meta-data android:name="io.flutter.embedding.android.ImpellerBackend" android:value="none"/>
    <meta-data android:name="io.flutter.embedding.android.EnableImpeller" android:value="false"/>

AXML 关键性质（本脚本依赖）：
  · 元素是线性 chunk 流，**没有全局偏移索引** ⇒ 插入/删除只需改各 chunk 的 size
    与文件头 size，不需要修正任何指针。
  · 属性识别靠 RES_XML_RESOURCE_MAP（索引 == 字符串池索引）。
    ⇒ android:name / android:value 必须复用**池里已有的**字符串索引；
      新增字符串只能追加到池尾（resmap 长度不变，前 N 项映射不受影响）。
"""
from __future__ import annotations

import struct
import sys

NS_ANDROID = "http://schemas.android.com/apk/res/android"

STR_POOL = 0x0001
RES_MAP = 0x0180
START_NS = 0x0100
END_NS = 0x0101
START_EL = 0x0102
END_EL = 0x0103

TYPE_STRING = 0x03

NO_ENTRY = 0xFFFFFFFF

# 我们要注入的两个 meta-data
INJECT = [
    ("io.flutter.embedding.android.ImpellerBackend", "none"),
    ("io.flutter.embedding.android.EnableImpeller", "false"),
]


class Pool:
    """解析 / 重建 UTF-16 字符串池。"""

    def __init__(self, buf: bytearray, off: int):
        self.off = off
        (self.type, self.hsize, self.size) = struct.unpack_from("<HHI", buf, off)
        assert self.type == STR_POOL, "在 %d 处不是字符串池 (%#x)" % (off, self.type)
        (self.count, self.style_count, self.flags,
         self.strings_start, self.styles_start) = struct.unpack_from("<IIIII", buf, off + 8)
        self.is_utf8 = bool(self.flags & (1 << 8))
        assert not self.is_utf8, "本脚本只处理 UTF-16 池（此 manifest 不是）"
        self.offsets = list(struct.unpack_from("<%dI" % self.count, buf, off + self.hsize))
        self.buf = buf
        self.strings: list[str] = []
        for i in range(self.count):
            p = off + self.strings_start + self.offsets[i]
            n = struct.unpack_from("<H", buf, p)[0]
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", buf, p + 2)[0]
                s = buf[p + 4: p + 4 + n * 2].decode("utf-16-le", "replace")
            else:
                s = buf[p + 2: p + 2 + n * 2].decode("utf-16-le", "replace")
            self.strings.append(s)
        self.end = off + self.size          # 原池 chunk 的结束偏移

    def find(self, s: str) -> int:
        try:
            return self.strings.index(s)
        except ValueError:
            return -1

    def add(self, s: str) -> int:
        """追加字符串，返回其索引（若已存在则直接返回）。"""
        i = self.find(s)
        if i >= 0:
            return i
        self.strings.append(s)
        return len(self.strings) - 1

    def build(self) -> bytes:
        n = len(self.strings)
        hsize = 28
        strings_start = hsize + 4 * n
        strings_start = (strings_start + 3) & ~3      # 4 字节对齐

        blob = bytearray()
        offsets = []
        for s in self.strings:
            offsets.append(len(blob))
            b = s.encode("utf-16-le")
            ln = len(s)
            if ln > 0x7FFF:
                blob += struct.pack("<H", (ln >> 16) | 0x8000) + struct.pack("<H", ln & 0xFFFF)
            else:
                blob += struct.pack("<H", ln)
            blob += b
            blob += b"\x00\x00"                       # NUL 结尾

        size = strings_start + len(blob)
        size = (size + 3) & ~3
        blob += b"\x00" * (size - strings_start - len(blob))

        out = bytearray()
        out += struct.pack("<HHI", STR_POOL, hsize, size)
        out += struct.pack("<IIIII", n, 0, self.flags, strings_start, 0)
        for o in offsets:
            out += struct.pack("<I", o)
        out += blob
        assert len(out) == size, (len(out), size)
        return bytes(out)


def make_start_element(line: int, name_idx: int, attrs: list[tuple[int, int, int]]) -> bytes:
    """attrs: [(attr_name_idx, raw_value_idx, typed_data_idx_or_value, data_type)]"""
    body = bytearray()
    body += struct.pack("<II", NO_ENTRY, name_idx)          # ns, name
    body += struct.pack("<HHH", 20, 20, len(attrs))         # attrStart, attrSize, attrCount
    body += struct.pack("<HHH", 0, 0, 0)                    # idIndex, classIndex, styleIndex
    for aname, araw, adata, atype in attrs:
        body += struct.pack("<III", NO_ENTRY, aname, araw)
        body += struct.pack("<HBBI", 8, 0, atype, adata)
    size = 16 + len(body)
    out = bytearray()
    out += struct.pack("<HHI", START_EL, 16, size)
    out += struct.pack("<II", line, NO_ENTRY)               # lineNumber, comment
    out += body
    assert len(out) == size, (len(out), size)
    return bytes(out)


def make_end_element(line: int, name_idx: int) -> bytes:
    out = bytearray()
    out += struct.pack("<HHI", END_EL, 16, 24)
    out += struct.pack("<II", line, NO_ENTRY)
    out += struct.pack("<II", NO_ENTRY, name_idx)
    assert len(out) == 24
    return bytes(out)


def find_application(buf: bytes, pool: Pool) -> tuple[int, int]:
    """返回 (application START_ELEMENT 的偏移, 该元素的 size)。"""
    off = 8
    app_name = pool.find("application")
    assert app_name >= 0, "池里没有 'application'"
    while off < len(buf):
        (ctype, chsize, csize) = struct.unpack_from("<HHI", buf, off)
        if csize == 0:
            break
        if ctype == START_EL:
            name = struct.unpack_from("<I", buf, off + 20)[0]
            if name == app_name:
                return off, csize
        off += csize
    raise SystemExit("找不到 <application> 节点")


def main():
    src, dst = sys.argv[1], sys.argv[2]
    buf = bytearray(open(src, "rb").read())
    (ftype, fhsize, fsize) = struct.unpack_from("<HHI", buf, 0)
    assert ftype == 0x0003, "不是 AXML"
    print("AXML: %d 字节, headerSize=%d, 声明 size=%d" % (len(buf), fhsize, fsize))

    # --- 定位字符串池 ---
    off = fhsize
    pool = None
    while off < len(buf):
        (ctype, chsize, csize) = struct.unpack_from("<HHI", buf, off)
        if csize == 0:
            break
        if ctype == STR_POOL:
            pool = Pool(buf, off)
            break
        off += csize
    assert pool is not None, "找不到字符串池"
    print("字符串池: %d 条, UTF-16=%s, chunk=[%d,%d)" % (pool.count, not pool.is_utf8, pool.off, pool.end))

    # --- 解析必需字符串 ---
    ns_idx = pool.find(NS_ANDROID)
    md_idx = pool.find("meta-data")
    pname = pool.find("name")
    pvalue = pool.find("value")
    print("必需字符串: ns_android=%d meta-data=%d name=%d value=%d" % (ns_idx, md_idx, pname, pvalue))
    for label, idx in (("android ns", ns_idx), ("meta-data", md_idx),
                       ("android:name", pname), ("android:value", pvalue)):
        assert idx >= 0, "池里缺少 %s —— 不能安全注入" % label

    # --- 追加新字符串（只追加到尾部，resmap 前 N 项不受影响）---
    before = pool.count
    plan = []
    for key, val in INJECT:
        ki = pool.add(key)
        vi = pool.add(val)
        plan.append((ki, vi, key, val))
    print("追加字符串: %d -> %d 条" % (before, pool.count))
    for ki, vi, k, v in plan:
        print("    + [%d] %s  =  [%d] %s" % (ki, k, vi, v))

    new_pool = pool.build()

    # --- 定位 <application> ---
    app_off, app_size = find_application(bytes(buf), pool)
    print("<application> 元素 @ %d, size=%d" % (app_off, app_size))
    assert app_off > pool.end, "无法插入（application 在池之前？）"

    # --- 构造注入片段 ---
    inject_buf = bytearray()
    for ki, vi, k, v in plan:
        inject_buf += make_start_element(
            1, md_idx,
            [(pname, ki, ki, TYPE_STRING),      # android:name = <key>
             (pvalue, vi, vi, TYPE_STRING)])    # android:value = <val>
        inject_buf += make_end_element(1, md_idx)
        print("    注入 meta-data %s=%s  (%d 字节)" % (k, v, len(inject_buf)))
    inject_buf = bytes(inject_buf)

    # --- 组装：A(头+resmap+...到池起点) + 新池 + C(池终点..插入点) + 注入 + D(插入点..尾) ---
    seg_a = bytes(buf[0:pool.off])
    seg_c = bytes(buf[pool.end:app_off + app_size])       # 到 application 元素末尾
    seg_d = bytes(buf[app_off + app_size:])

    out = bytearray()
    out += seg_a
    out += new_pool
    out += seg_c
    out += inject_buf
    out += seg_d

    # 文件头 size 更新
    struct.pack_into("<I", out, 4, len(out))
    assert len(out) == len(buf) - (pool.end - pool.off) + len(new_pool) + len(inject_buf)

    open(dst, "wb").write(out)
    print()
    print("写出: %s  (%d 字节, 原 %d 字节, +%d)" % (dst, len(out), len(buf), len(out) - len(buf)))

    # --- 自检：重新解析 ---
    print()
    print("=== 自检（重新解析注入后的 AXML）===")
    import subprocess
    r = subprocess.run([sys.executable, __file__.replace("inject_impeller.py", "axml.py"), dst],
                       capture_output=True, text=True, errors="replace")
    lines = r.stdout.splitlines()
    print("  解析行数:", len(lines), "  (stderr:", r.stderr.strip()[:80] or "无", ")")
    hits = [l for l in lines if "Impeller" in l]
    for h in hits:
        print("  ", h.strip())
    if not hits:
        raise SystemExit("!! 自检失败：注入的 meta-data 没出现在解析结果里")


if __name__ == "__main__":
    main()
