#!/usr/bin/env python3
"""Dump / filter ELF symbols for both 32-bit and 64-bit objects.

Why this exists: `strings | grep` CANNOT tell whether a `__weak` function was
overridden by a strong one.  Module links use `ld -r`, so a weak definition's
body survives in .text as dead code and its string literals stay in .rodata even
when every reference resolved to the strong definition.  The symbol table is the
only reliable answer, and `nm` is not guaranteed to be present for both classes
on every host -- so parse the ELF here.

Usage:
    python3 elf_symbols.py <file> [--grep REGEX] [--all]
                           [--undef] [--defined]

Default output is one line per matching symbol:
    <name>  <bind> <type> <shndx> <value> <size>

Examples:
    # is the strong mtk_wcn_get_consys_ic_ops linked in?
    python3 elf_symbols.py wmt_drv.ko --grep 'consys_ic_ops'
    # what does it still need from the kernel?
    python3 elf_symbols.py wmt_drv.ko --undef
"""

import argparse
import re
import struct
import sys


class Elf:
    """Minimal ELF reader good enough for .symtab + .dynsym + shstrtab."""

    SHT_SYMTAB = 2
    SHT_STRTAB = 3
    SHT_DYNSYM = 11

    def __init__(self, path):
        self.path = path
        with open(path, "rb") as fh:
            self.d = fh.read()
        if self.d[:4] != b"\x7fELF":
            raise SystemExit("%s: not an ELF file" % path)
        self.cls = self.d[4]           # 1 = 32-bit, 2 = 64-bit
        self.endian = self.d[5]        # 1 = little, 2 = big
        if self.endian != 1:
            raise SystemExit("%s: only little-endian supported" % path)
        self._parse_header()
        self._parse_sections()

    def _parse_header(self):
        d = self.d
        if self.cls == 2:
            (_t, _m, _v, _e, _po, shoff, _f, _eh, _pe, _pn,
             shentsize, shnum, shstrndx) = struct.unpack_from("<HHIQQQIHHHHHH", d, 16)
        else:
            (_t, _m, _v, _e, _po, shoff, _f, _eh, _pe, _pn,
             shentsize, shnum, shstrndx) = struct.unpack_from("<HHIIIIIHHHHHH", d, 16)
        self.shoff = shoff
        self.shentsize = shentsize
        self.shnum = shnum
        self.shstrndx = shstrndx

    def _parse_sections(self):
        d, secs = self.d, []
        for i in range(self.shnum):
            o = self.shoff + i * self.shentsize
            if self.cls == 2:
                s = struct.unpack_from("<IIQQQQIIQQ", d, o)
            else:
                s = struct.unpack_from("<IIIIIIIIII", d, o)
            secs.append(s)
        self.secs = secs
        self._shstr = secs[self.shstrndx][4]
        self.names = [self._str(self._shstr, s[0]) for s in secs]

    def _str(self, base, off):
        d = self.d
        e = d.index(b"\0", base + off)
        return d[base + off:e].decode("utf-8", "replace")

    def sections(self):
        for i, s in enumerate(self.secs):
            yield i, self.names[i], s

    def symbols(self):
        """Yield (name, bind, type, shndx, value, size) for every symbol."""
        for i, name, s in self.sections():
            if name not in (".symtab", ".dynsym"):
                continue
            strtab_off = self.secs[s[6]][4]
            entsize = s[9] or (24 if self.cls == 2 else 16)
            count = s[5] // entsize
            for k in range(count):
                o = s[4] + k * entsize
                if self.cls == 2:
                    st_name, st_info, _st_other, st_shndx, st_value, st_size = \
                        struct.unpack_from("<IBBHQQ", self.d, o)
                else:
                    st_name, st_value, st_size, st_info, _st_other, st_shndx = \
                        struct.unpack_from("<IIIBBH", self.d, o)
                yield (self._str(strtab_off, st_name),
                       st_info >> 4, st_info & 0xF, st_shndx, st_value, st_size)


BIND = {0: "LOCAL", 1: "GLOBAL", 2: "WEAK"}
TYPE = {0: "NOTYPE", 1: "OBJECT", 2: "FUNC", 3: "SECTION", 4: "FILE",
        5: "COMMON", 6: "TLS"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--grep", help="only symbols whose name matches this regex")
    ap.add_argument("--undef", action="store_true", help="only SHN_UNDEF symbols")
    ap.add_argument("--defined", action="store_true", help="only defined symbols")
    ap.add_argument("--all", action="store_true",
                    help="include LOCAL symbols (default: GLOBAL+WEAK only)")
    a = ap.parse_args(argv)

    e = Elf(a.file)
    rx = re.compile(a.grep) if a.grep else None
    print("%s: ELF%d, %d sections, table=%s"
          % (a.file, 32 if e.cls == 1 else 64, e.shnum,
             "present" if any(n in (".symtab", ".dynsym") for n in e.names)
             else "ABSENT (stripped)"))
    n = 0
    for (name, bind, typ, shndx, value, size) in e.symbols():
        if not name:
            continue
        if not a.all and bind == 0:
            continue
        if a.undef and shndx != 0:
            continue
        if a.defined and shndx == 0:
            continue
        if rx and not rx.search(name):
            continue
        where = "UND" if shndx == 0 else "%d" % shndx
        print("  %-46s %-6s %-7s %-5s 0x%-10x %d"
              % (name, BIND.get(bind, bind), TYPE.get(typ, typ), where, value, size))
        n += 1
    print("  -- %d matching symbol(s)" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
