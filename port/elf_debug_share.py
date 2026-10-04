#!/usr/bin/env python3
"""What is actually inside a .ko -- and what `strip -g` would remove.

Three numbers matter, and they are all different:

  file size    what has to travel over adb and fit on the partition
  debug size   what INSTALL_MOD_STRIP=1 / `strip -g` removes (never loaded)
  ALLOC size   SHF_ALLOC sections summed = what the kernel actually maps into
               module memory. THIS is the number that decides whether insmod
               can succeed, not the file size.

Handles both ELF classes so a 64-bit build can be compared against the 32-bit
stock modules pulled off the device.

    python3 port/elf_debug_share.py <file.ko> [...]
"""
import struct
import sys

DEBUG_PREFIXES = (".debug", ".zdebug", ".stab", ".gnu.debuglto")
SHF_ALLOC = 0x2


def parse(path):
    d = open(path, "rb").read()
    if d[:4] != b"\x7fELF":
        raise SystemExit("not an ELF: %s" % path)
    is64 = d[4] == 2
    if is64:
        (_t, _m, _v, _e, _po, shoff, _f, _eh, _pe, _pn, shentsize,
         shnum, shstrndx) = struct.unpack_from("<HHIQQQIHHHHHH", d, 16)
        fmt, esz_min = "<IIQQQQIIQQ", 64
    else:
        (_t, _m, _v, _e, _po, shoff, _f, _eh, _pe, _pn, shentsize,
         shnum, shstrndx) = struct.unpack_from("<HHIIIIIHHHHHH", d, 16)
        fmt, esz_min = "<IIIIIIIIII", 40
    secs = []
    for i in range(shnum):
        v = struct.unpack_from(fmt, d, shoff + i * shentsize)
        secs.append(dict(nameoff=v[0], type=v[1], flags=v[2], off=v[4],
                         size=v[5], entsize=v[9] if len(v) > 9 else 0))
    base = struct.unpack_from(fmt, d, shoff + shstrndx * shentsize)[4]
    for s in secs:
        end = d.index(b"\0", base + s["nameoff"])
        s["name"] = d[base + s["nameoff"]:end].decode("utf-8", "replace")
    return len(d), is64, secs


def human(n):
    return "%.2f MiB" % (n / 1048576.0) if n >= 1048576 else "%.1f KiB" % (n / 1024.0)


def report(path):
    total, is64, secs = parse(path)
    dbg = sum(s["size"] for s in secs
              if s["name"].startswith(DEBUG_PREFIXES) or s["name"] == ".line")
    alloc = sum(s["size"] for s in secs if s["flags"] & SHF_ALLOC)
    text = sum(s["size"] for s in secs if s["name"] in (".text", ".init.text"))
    kept = [n for n in (".modinfo", "__versions", ".symtab") if any(
        s["name"] == n for s in secs)]
    print("%s  [%s]" % (path, "ELF64/AArch64" if is64 else "ELF32/ARM"))
    print("   file      %10d  %-10s   strip -g removes %d (%s, %.0f%%)"
          % (total, human(total), dbg, human(dbg), 100.0 * dbg / total))
    print("   after -g  %10d  %-10s" % (total - dbg, human(total - dbg)))
    print("   ALLOC     %10d  %-10s   <- what insmod actually maps"
          % (alloc, human(alloc)))
    print("   .text     %10d  %-10s" % (text, human(text)))
    print("   kept      %s" % (", ".join(kept) or "<none found>"))
    missing = [n for n in (".modinfo", "__versions", ".symtab") if n not in kept]
    if missing:
        print("   WARNING   absent: %s" % ", ".join(missing))


for p in sys.argv[1:]:
    report(p)
    print()
