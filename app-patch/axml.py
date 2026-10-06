#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""极简 Android AXML (binary XML) 解析器/打印器 —— 用于查看与定位 meta-data 节点。

AXML 结构：
  文件头   RES_XML_TYPE(0x0003) headerSize=8
  ├ RES_STRING_POOL_TYPE (0x0001)   字符串池（UTF-8 或 UTF-16）
  ├ RES_XML_RESOURCE_MAP_TYPE(0x0180)  属性名 -> android 系统资源 id
  ├ RES_XML_START_NAMESPACE(0x0100)
  ├ RES_XML_START_ELEMENT (0x0102)  元素开始
  ├ RES_XML_END_ELEMENT   (0x0103)
  └ RES_XML_END_NAMESPACE (0x0101)
"""
from __future__ import annotations

import struct
import sys

RES_STRING_POOL = 0x0001
RES_XML_RESOURCE_MAP = 0x0180
RES_XML_START_NS = 0x0100
RES_XML_END_NS = 0x0101
RES_XML_START_EL = 0x0102
RES_XML_END_EL = 0x0103
RES_XML_CDATA = 0x0104

TYPES = {
    0x0001: "STRING_POOL", 0x0180: "RESOURCE_MAP",
    0x0100: "START_NS", 0x0101: "END_NS",
    0x0102: "START_ELEMENT", 0x0103: "END_ELEMENT", 0x0104: "CDATA",
}

# Res_value dataType
DT = {
    0x00: "NULL", 0x01: "REFERENCE", 0x02: "ATTRIBUTE", 0x03: "STRING",
    0x04: "FLOAT", 0x05: "DIMENSION", 0x06: "FRACTION", 0x07: "DYNAMIC_REF",
    0x08: "INT_DEC", 0x10: "INT_HEX", 0x11: "INT_BOOLEAN", 0x12: "INT_COLOR_ARGB8",
    0x1c: "INT_COLOR_RGB8", 0x1d: "INT_COLOR_ARGB4", 0x1e: "INT_COLOR_RGB4",
}


class StringPool:
    def __init__(self, data: bytes, off: int):
        (self.type, self.hsize, self.size) = struct.unpack_from("<HHI", data, off)
        (self.string_count, self.style_count, self.flags,
         self.strings_start, self.styles_start) = struct.unpack_from("<IIIII", data, off + 8)
        self.is_utf8 = bool(self.flags & (1 << 8))
        self.offsets = list(struct.unpack_from("<%dI" % self.string_count, data, off + 28))
        self.base = off
        self.data = data
        self.cache: dict[int, str] = {}

    def get(self, idx: int) -> str:
        if idx < 0 or idx >= self.string_count:
            return "<bad idx %d>" % idx
        if idx in self.cache:
            return self.cache[idx]
        p = self.base + self.strings_start + self.offsets[idx]
        if self.is_utf8:
            # UTF-8 池：u8 len, u8 hi_len, 再是 len 字节，末尾 0
            n = self.data[p]
            if n & 0x80:
                n = ((n & 0x7F) << 8) | self.data[p + 1]
                p += 1
            s = self.data[p + 1: p + 1 + n].decode("utf-8", "replace")
        else:
            # UTF-16 池：u16 len（含扩展），再是 len*2 字节，末尾 u16 0
            n = struct.unpack_from("<H", self.data, p)[0]
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", self.data, p + 2)[0]
                s = self.data[p + 4: p + 4 + n * 2].decode("utf-16-le", "replace")
            else:
                s = self.data[p + 2: p + 2 + n * 2].decode("utf-16-le", "replace")
        self.cache[idx] = s
        return s


def parse(data: bytes):
    """返回 (ranges, ret)：ranges = 元素路径范围列表，ret = 打印文本。"""
    (typ, hsize, size) = struct.unpack_from("<HHI", data, 0)
    assert typ == 0x0003, "不是 AXML：type=%#x" % typ
    out = []
    out.append("AXML 总长 %d 字节" % len(data))

    off = hsize
    pool: StringPool | None = None
    while off < len(data):
        (ctype, chsize, csize) = struct.unpack_from("<HHI", data, off)
        if csize == 0:
            break
        if ctype == RES_STRING_POOL:
            pool = StringPool(data, off)
            out.append("  字符串池 @%d  条目=%d  UTF-8=%s  size=%d"
                       % (off, pool.string_count, pool.is_utf8, csize))
        elif ctype == RES_XML_RESOURCE_MAP:
            n = (csize - chsize) // 4
            ids = struct.unpack_from("<%dI" % n, data, off + chsize)
            out.append("  资源映射 @%d  %d 项" % (off, n))
        elif ctype in (RES_XML_START_EL, RES_XML_CDATA):
            (line, comment) = struct.unpack_from("<II", data, off + chsize - 8 - 0 + 0) if chsize >= 8 else (0, 0)
        off += csize
    return out


def main():
    path = sys.argv[1]
    raw = open(path, "rb").read()
    (typ, hsize, size) = struct.unpack_from("<HHI", raw, 0)
    print("type=%#x hsize=%d size=%d filelen=%d" % (typ, hsize, size, len(raw)))

    # 第一遍：找字符串池
    off = hsize
    pool = None
    while off < len(raw):
        (ctype, chsize, csize) = struct.unpack_from("<HHI", raw, off)
        if csize == 0:
            break
        if ctype == RES_STRING_POOL:
            pool = StringPool(raw, off)
            break
        off += csize
    print("字符串池条目=%d utf8=%s" % (pool.string_count, pool.is_utf8))

    # 第二遍：遍历事件
    off = hsize
    depth = 0
    while off < len(raw):
        (ctype, chsize, csize) = struct.unpack_from("<HHI", raw, off)
        if csize == 0:
            break
        if ctype == RES_XML_START_EL:
            (line, comment) = struct.unpack_from("<II", raw, off + 8)
            (ns, name, attrStart, attrSize, attrCount, idIdx, clsIdx, styleIdx) = \
                struct.unpack_from("<IIHHHHHH", raw, off + 16)
            print("%s<%s>   (chunk@%d line=%d attrs=%d)"
                  % ("  " * depth, pool.get(name), off, line, attrCount))
            aoff = off + 16 + attrStart
            for i in range(attrCount):
                p = aoff + i * attrSize
                (ans, aname, araw, vsize, vres0, vtype, vdata) = \
                    struct.unpack_from("<IIIHBBI", raw, p)[:7]
                key = pool.get(aname)
                if vtype == 0x03:
                    val = pool.get(vdata)
                else:
                    val = "%s(%d)" % (DT.get(vtype, hex(vtype)), vdata)
                print("%s   @%s = %s   [raw=%s type=%s]"
                      % ("  " * depth, key, val, pool.get(araw) if araw != 0xFFFFFFFF else "-", hex(vtype)))
            depth += 1
        elif ctype == RES_XML_END_EL:
            depth -= 1
        elif ctype == RES_XML_START_NS:
            (line, comment) = struct.unpack_from("<II", raw, off + 8)
            (pns, pref, uri) = struct.unpack_from("<III", raw, off + 16)
            print("  ns: %s -> %s" % (pool.get(pref), pool.get(uri)))
        off += csize


if __name__ == "__main__":
    main()
