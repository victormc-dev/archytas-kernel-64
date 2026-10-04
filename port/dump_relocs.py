#!/usr/bin/env python3
"""Dump AArch64 relocation types from a .ko.

Used to prove whether ADRP (R_AARCH64_ADR_PREL_PG_HI21/_NC, 275/276)
relocations are present -- the target kernel has CONFIG_ARM64_ERRATUM_843419=y
and therefore refuses to load any module containing them (-ENOEXEC,
"unsupported RELA relocation: 275").

Usage:
    python3 dump_relocs.py <module.ko> [--targets]
"""
import struct, sys, collections

R = {
    257: "ABS64", 258: "ABS32", 259: "ABS16",
    260: "PREL64", 261: "PREL32", 262: "PREL16",
    263: "MOVW_UABS_G0", 264: "MOVW_UABS_G0_NC",
    265: "MOVW_UABS_G1", 266: "MOVW_UABS_G1_NC",
    267: "MOVW_UABS_G2", 268: "MOVW_UABS_G2_NC", 269: "MOVW_UABS_G3",
    273: "LD_PREL_LO19", 274: "ADR_PREL_LO21",
    275: "ADR_PREL_PG_HI21", 276: "ADR_PREL_PG_HI21_NC",
    277: "ADD_ABS_LO12_NC", 278: "LDST8_ABS_LO12_NC",
    279: "LDST16_ABS_LO12_NC", 280: "LDST32_ABS_LO12_NC",
    281: "LDST64_ABS_LO12_NC", 282: "LDST128_ABS_LO12_NC",
    283: "TSTBR14", 284: "CONDBR19", 285: "JUMP26", 286: "CALL26",
}


def load(path):
    d = open(path, "rb").read()
    if d[:4] != b"\x7fELF" or d[4] != 2:
        raise SystemExit("not a 64-bit ELF: %s" % path)
    (_t, emach, _v, _e, _po, shoff, _f,
     _eh, _pe, _pn, shentsize, shnum, shstrndx) = struct.unpack_from("<HHIQQQIHHHHHH", d, 16)
    secs = []
    for i in range(shnum):
        o = shoff + i * shentsize
        name, typ, flags, addr, off, size, link, info, align, entsize = \
            struct.unpack_from("<IIQQQQIIQQ", d, o)
        secs.append(dict(name=name, typ=typ, off=off, size=size, link=link,
                         entsize=entsize))
    strtab_off = secs[shstrndx]["off"]

    def sname(x):
        e = d.index(b"\0", strtab_off + x)
        return d[strtab_off + x:e].decode("utf-8", "replace")
    return d, secs, sname


def symbols(d, secs, sname, symtab_index):
    s = secs[symtab_index]
    # linked strtab
    strtab = secs[s["link"]]["off"]
    out = []
    for k in range(s["size"] // 24):
        st_name, st_info, st_other, st_shndx, st_value, st_size = \
            struct.unpack_from("<IBBHQQ", d, s["off"] + k * 24)
        e = d.index(b"\0", strtab + st_name)
        out.append(dict(name=d[strtab + st_name:e].decode("utf-8", "replace"),
                        shndx=st_shndx, value=st_value, info=st_info))
    return out


def sym_label(syms, secs, sname, i):
    """Human label for symbol index i.

    IMPORTANT: section symbols (STT_SECTION) conventionally carry st_name == 0,
    so they come out as an empty string. Printing them as "<unnamed/section>"
    hides the single most useful fact about an ADRP relocation. Resolve the
    section name from st_shndx instead, so the output reads "[sec].text" etc.
    """
    if i >= len(syms):
        return "?"
    s = syms[i]
    if s["name"]:
        return s["name"]
    if s["shndx"] == 0:
        return "<undef#0>"
    if s["shndx"] == 0xffff:
        return "<ABS>"
    if s["shndx"] < len(secs):
        return "[sec]%s" % sname(secs[s["shndx"]]["name"])
    return "shndx=%s" % s["shndx"]


def main():
    path = sys.argv[1]
    want_targets = "--targets" in sys.argv
    d, secs, sname = load(path)
    symtab_i = next((i for i, s in enumerate(secs) if s["typ"] == 2), None)
    syms = symbols(d, secs, sname, symtab_i) if symtab_i is not None else []

    total = collections.Counter()
    adrp_targets = collections.Counter()
    per_sec = collections.OrderedDict()
    for s in secs:
        if s["typ"] not in (4, 9) or not sname(s["name"]).startswith(".rel"):
            continue
        esz = s["entsize"] or 24
        c = collections.Counter()
        for k in range(s["size"] // esz):
            o = s["off"] + k * esz
            r_offset, r_info, r_addend = struct.unpack_from("<QQq", d, o)
            t = r_info & 0xFFFFFFFF
            c[t] += 1
            total[t] += 1
            if t in (275, 276) and syms:
                si = r_info >> 32
                if si < len(syms):
                    adrp_targets[sym_label(syms, secs, sname, si)] += 1
        if c:
            per_sec[sname(s["name"])] = c

    print("=== %s ===" % path)
    for nm, c in per_sec.items():
        parts = ", ".join("%s(%d)x%d" % (R.get(t, "?"), t, n) for t, n in c.most_common())
        print("  %-24s %s" % (nm, parts))
    print("  --- totals ---")
    for t, n in total.most_common():
        print("     %-24s %s = %d" % (R.get(t, "?"), t, n))
    if want_targets and adrp_targets:
        print("  --- ADRP target symbols (top 15) ---")
        for nm, n in adrp_targets.most_common(15):
            print("     %6d  %s" % (n, nm or "<unnamed/section>"))
    bad = total.get(275, 0) + total.get(276, 0)
    print("  >>> ADRP(275/276) count = %d  -> %s" % (
        bad,
        "needs the kernel-side ARCHYTAS_ADRP_RELOC fix "
        "(port/build_connectivity_modules.sh step 2e); without it the module "
        "loader rejects it with -ENOEXEC" if bad
        else "no ADRP, loadable even on an unpatched kernel"))


if __name__ == "__main__":
    main()
