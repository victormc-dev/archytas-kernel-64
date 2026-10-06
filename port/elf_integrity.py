#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Structural ELF check: does every header point INSIDE the file?

Reading the first few bytes is not enough.  A file whose tail was lost -- a
truncated copy, a dropped ext4 extent, a wrong `dd` count -- still opens with a
perfectly good `\\x7fELF` magic and the right EI_CLASS, so header-only checks
pass happily while the library is completely unusable.

The cheap, decisive test is to walk the header tables and require that nothing
they describe falls past EOF:

    * e_phoff + e_phnum*e_phentsize <= filesize     (program header table)
    * e_shoff + e_shnum*e_shentsize <= filesize     (section header table)
    * every PT_LOAD    : p_offset + p_filesz <= filesize
    * every PT_DYNAMIC : p_offset + p_filesz <= filesize

A real ELF always satisfies all four.  A truncated one fails loudly -- and that
matters, because the linker reports a truncated *dependency* as
`dlopen failed: ... unknown`, which is indistinguishable from a hundred other
problems.  That is exactly how 19 of the 29 /vendor/lib64 libraries shipped
short by up to 63 KB and put SurfaceFlinger into a crash loop:

    E libEGL : load_driver(/vendor/lib64/egl/libEGL_mtk.so): unknown
    F libEGL : couldn't find an OpenGL ES implementation

Usage:
  elf_integrity.py <file-or-dir> [...]     # exit 1 if anything is bad
"""
import os
import struct
import sys

ELF_MAGIC = b'\x7fELF'
PT_LOAD, PT_DYNAMIC = 1, 2
PN_XNUM = 0xFFFF


def check(data):
    """Return (ok, reason).  `reason` is '' when ok."""
    if len(data) < 64 or data[:4] != ELF_MAGIC:
        return False, "not an ELF file"
    bits = 64 if data[4] == 2 else 32
    endian = '<' if data[5] == 1 else '>'
    if bits == 64:
        e_phoff, e_shoff = struct.unpack_from(endian + 'QQ', data, 32)
        e_phentsize, e_phnum, e_shentsize, e_shnum = struct.unpack_from(
            endian + 'HHHH', data, 54)
        ph_fmt, ph_len = endian + 'IIQQQQQQ', 56
    else:
        e_phoff, e_shoff = struct.unpack_from(endian + 'II', data, 28)
        e_phentsize, e_phnum, e_shentsize, e_shnum = struct.unpack_from(
            endian + 'HHHH', data, 42)
        ph_fmt, ph_len = endian + 'IIIIIIII', 32
    n = len(data)

    # e_shnum == 0 means "real count is in section 0" (large-file form); we do
    # not need the count itself, only a bound, so treat it as "table may run to
    # the end of file" and skip the check in that rare case.
    if e_phoff and e_phnum != PN_XNUM and e_phoff + e_phnum * e_phentsize > n:
        return False, ("program header table past EOF "
                       "(e_phoff=%#x + %d*%d > %d)" % (e_phoff, e_phnum, e_phentsize, n))
    if e_shoff and e_shnum and e_shoff + e_shnum * e_shentsize > n:
        return False, ("section header table past EOF "
                       "(e_shoff=%#x + %d*%d > %d)" % (e_shoff, e_shnum, e_shentsize, n))

    for i in range(e_phnum if e_phnum != PN_XNUM else 0):
        off = e_phoff + i * e_phentsize
        if off + ph_len > n:
            return False, "program header %d past EOF" % i
        ph = struct.unpack_from(ph_fmt, data, off)
        if bits == 64:
            p_type, _, p_offset, p_filesz = ph[0], ph[1], ph[2], ph[5]
        else:
            p_type, p_offset, p_filesz = ph[0], ph[1], ph[4]
        if p_type in (PT_LOAD, PT_DYNAMIC) and p_offset + p_filesz > n:
            kind = "PT_LOAD" if p_type == PT_LOAD else "PT_DYNAMIC"
            return False, ("%s past EOF (off=%#x + %d > %d, short by %d B)"
                           % (kind, p_offset, p_filesz, n, p_offset + p_filesz - n))
    return True, ""


def walk(target):
    if os.path.isdir(target):
        for root, _d, files in os.walk(target):
            for fn in sorted(files):
                yield os.path.join(root, fn)
    else:
        yield target


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    bad = 0
    checked = 0
    for target in sys.argv[1:]:
        for path in walk(target):
            with open(path, 'rb') as fh:
                data = fh.read()
            if data[:4] != ELF_MAGIC:
                continue                     # not a library; not our business
            checked += 1
            ok, why = check(data)
            if not ok:
                bad += 1
                print("  !! %-58s %d B  %s" % (path, len(data), why))
    print("\n检查 %d 个 ELF，问题 %d 个" % (checked, bad))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
