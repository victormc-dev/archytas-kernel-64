#!/usr/bin/env python3
"""Dump AArch64 relocation types from a .ko, to prove whether ADRP (275/276)
relocations are present (i.e. whether -mcmodel=large was applied)."""
import struct, sys, collections

R = {
    257: "ABS64", 258: "ABS32", 259: "ABS16",
    260: "PREL64", 261: "PREL32", 262: "PREL16",
    273: "LD_PREL_LO19", 274: "ADR_PREL_LO21",
    275: "ADR_PREL_PG_HI21", 276: "ADR_PREL_PG_HI21_NC",
    277: "ADD_ABS_LO12_NC", 278: "LDST8_ABS_LO12_NC",
    279: "LDST16_ABS_LO12_NC", 280: "LDST32_ABS_LO12_NC",
    281: "LDST64_ABS_LO12_NC", 282: "LDST128_ABS_LO12_NC",
    283: "TSTBR14", 284: "CONDBR19", 285: "JUMP26", 286: "CALL26",
    311: "MOVW_UABS_G0", 312: "MOVW_UABS_G0_NC",
}

def main(path):
    d = open(path, "rb").read()
    if d[:4] != b"\x7fELF":
        print("not ELF"); return
    cls = d[4]
    if cls != 2:
        print("not 64-bit ELF"); return
    (etype, emach, ever, entry, phoff, shoff, eflags,
     ehsize, phentsize, phnum, shentsize, shnum, shstrndx) = struct.unpack_from("<HHIQQQIHHHHHH", d, 16)
    print("  ELF machine=%d (183=AArch64)  class=%d (2=64bit)" % (emach, cls))
    secs = []
    for i in range(shnum):
        o = shoff + i * shentsize
        name, typ, flags, addr, off, size, link, info, align, entsize = struct.unpack_from("<IIQQQQIIQQ", d, o)
        secs.append(dict(name=name, typ=typ, off=off, size=size, entsize=entsize, link=link, info=info))
    shstr = secs[shstrndx]
    def sname(x):
        e = d.index(b"\0", shstr["off"] + x)
        return d[shstr["off"] + x:e].decode(errors="replace")
    total = collections.Counter()
    per_sec = collections.OrderedDict()
    for i, s in enumerate(secs):
        if s["typ"] not in (4, 9):   # SHT_RELA / SHT_REL
            continue
        nm = sname(s["name"])
        if not nm.startswith(".rel"):
            continue
        esz = s["entsize"] or 24
        n = s["size"] // esz
        c = collections.Counter()
        for k in range(n):
            o = s["off"] + k * esz
            r_offset, r_info, r_addend = struct.unpack_from("<QQq", d, o)
            c[r_info & 0xFFFFFFFF] += 1
            total[r_info & 0xFFFFFFFF] += 1
        if c:
            per_sec[nm] = c
    print("=== %s ===" % path)
    for nm, c in per_sec.items():
        parts = ", ".join("%s(%d)x%d" % (R.get(t, "?"), t, n) for t, n in c.most_common())
        print("  %-22s %s" % (nm, parts))
    print("  --- totals ---")
    for t, n in total.most_common():
        print("     %-24s %s = %d" % (R.get(t, "?"), t, n))
    bad = total.get(275, 0) + total.get(276, 0)
    print("  >>> ADRP(275/276) count = %d  -> %s" % (
        bad, "MODULE WILL BE REJECTED" if bad else "no ADRP, OK"))

if __name__ == "__main__":
    main(sys.argv[1])
