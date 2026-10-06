#!/usr/bin/env python3
"""Instruction-level diff of one function across two builds of a .ko.

AArch64 is fixed-width (4 bytes/insn).  Kernel modules are built with
-mcmodel=large-ish / PLT-less BL and ADRP whose immediates are filled by
relocations, so in a linked-but-unrelocated .ko every BL is literally
0x94000000 and every ADRP 0x90000000.  That means the raw 32-bit word
stream of a function is directly comparable across two builds WITHOUT
normalising addresses: if the compiler moved code, difflib will show it.

Used to settle why OUR wlan_drv_gen4m.ko deadlocks in
scanGetCurrentEssChnlList while agui's reference build does not.

Usage:
    python diff_func_insns.py <func> <ko_a> <ko_b>
"""
import difflib
import struct
import sys

import audit_wmt_fb_workaround as A


def func_words(path, fname):
    e = A.ElfRel(path)
    sym = e.get(fname)
    if sym is None:
        return None, None, None
    shndx, val, size, *_ = sym
    sec = [s for s in e.secs if s["idx"] == shndx][0]
    base = sec["offset"] + val
    n = size // 4
    words = [struct.unpack_from("<I", e.d, base + k * 4)[0] for k in range(n)]
    # relocation map r_offset -> symbol (so a diff line names the callee)
    rel = {}
    for r_off, sname, _rtype, _ad in e.relas_for(shndx):
        if sname:
            rel.setdefault(r_off, sname)
    return val, words, rel


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        raise SystemExit(2)
    fname, pa, pb = sys.argv[1:4]
    va, wa, ra = func_words(pa, fname)
    vb, wb, rb = func_words(pb, fname)
    na, nb = pa.split("/")[-1], pb.split("/")[-1]
    if wa is None:
        print("!! %s not in %s" % (fname, na)); raise SystemExit(1)
    if wb is None:
        print("!! %s not in %s" % (fname, nb)); raise SystemExit(1)

    print("A = %s  [%#x..%#x)  %d insns" % (na, va, va + len(wa) * 4, len(wa)))
    print("B = %s  [%#x..%#x)  %d insns" % (nb, vb, vb + len(wb) * 4, len(wb)))
    print()

    # Semantic fingerprint: which external symbols does each build reference?
    # Robust against optimisation-level / register-allocation noise.
    sa = sorted(set(ra.values()))
    sb = sorted(set(rb.values()))
    only_a = [s for s in sa if s not in sb]
    only_b = [s for s in sb if s not in sa]
    print("symbols only in A (%d): %s" % (len(only_a), ", ".join(only_a) or "-"))
    print("symbols only in B (%d): %s" % (len(only_b), ", ".join(only_b) or "-"))
    print("shared symbols  (%d): %s" % (
        len([s for s in sa if s in sb]), ", ".join(s for s in sa if s in sb) or "-"))
    print()

    sm = difflib.SequenceMatcher(a=wa, b=wb, autojunk=False)
    ratio = sm.ratio()
    print("similarity = %.4f" % ratio)
    print()

    ai = bi = 0
    # index words by original position for address lookup
    lines = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            # collapse long runs
            run = i2 - i1
            if run > 6:
                lines.append("    = %#08x .. %#08x  (%d identical insns)" % (
                    va + i1 * 4, va + (i2 - 1) * 4, run))
            else:
                for k in range(run):
                    lines.append("    = %#08x  %08x" % (va + (i1 + k) * 4, wa[i1 + k]))
            continue
        if tag == "replace":
            for k in range(i1, i2):
                off = va + k * 4
                note = ("   ; " + ra[off]) if off in ra else ""
                lines.append("  A-%#08x  %08x%s" % (off, wa[k], note))
            for k in range(j1, j2):
                off = vb + k * 4
                note = ("   ; " + rb[off]) if off in rb else ""
                lines.append("  B+%#08x  %08x%s" % (off, wb[k], note))
        elif tag == "delete":
            for k in range(i1, i2):
                off = va + k * 4
                note = ("   ; " + ra[off]) if off in ra else ""
                lines.append("  A-%#08x  %08x%s   (deleted)" % (off, wa[k], note))
        elif tag == "insert":
            for k in range(j1, j2):
                off = vb + k * 4
                note = ("   ; " + rb[off]) if off in rb else ""
                lines.append("  B+%#08x  %08x%s   (inserted)" % (off, wb[k], note))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
