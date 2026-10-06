#!/usr/bin/env python3
"""Authoritative AArch64 disassembly of one .ko function (Capstone based).

`port/disasm_arm64.py` was a hand-rolled decoder written to settle a single
yes/no question about a `schedule_work()` call; it decodes only branch/ADR
forms and prints "LD/ST" for everything else.  That is not enough to reason
about a *control-flow* defect, so this script uses Capstone instead and prints
real mnemonics plus the relocation attached to each instruction.

Requires Capstone (installed into the managed venv):
    <venv>/Scripts/pip install capstone

Usage:
    python disasm_capstone.py <symbol> <ko> [<ko> ...]
    python disasm_capstone.py <symbol> <ko> --lo 0x.. --hi 0x..   # window
"""
import argparse
import struct
import sys

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN, CS_OPT_DETAIL

import audit_wmt_fb_workaround as A


def disasm_func(path, fname, lo=None, hi=None, show_reloc=True):
    e = A.ElfRel(path)
    sym = e.get(fname)
    if sym is None:
        print("  %s: symbol not found in %s" % (fname, path))
        return None
    shndx, val, size = sym[0], sym[1], sym[2]
    sec = [s for s in e.secs if s["idx"] == shndx][0]
    base = sec["offset"] + val
    relmap = {}
    for r_off, sname, rtype, _ad in e.relas_for(shndx):
        relmap.setdefault(r_off, (sname, rtype, _ad))

    start = val if lo is None else val + (lo - val)
    end = val + size if hi is None else hi
    code = e.d[base + (start - val): base + (end - val)]

    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    md.detail = True

    print("  %s  [%#x..%#x)  %d bytes   %s" % (
        fname, val, val + size, size, path.replace("\\", "/").split("/")[-1]))
    out = []
    for ins in md.disasm(code, start):
        rel = ""
        if show_reloc and ins.address in relmap:
            sname, rtype, radd = relmap[ins.address]
            rel = "   ; reloc %s type=%d addend=%d" % (sname or "<sec>", rtype, radd)
        txt = "    %#08x: %-8s %-8s %s%s" % (ins.address, ins.bytes.hex(),
                                             ins.mnemonic, ins.op_str, rel)
        out.append(txt)
        print(txt)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol")
    ap.add_argument("kos", nargs="+")
    ap.add_argument("--lo", type=lambda s: int(s, 0), default=None)
    ap.add_argument("--hi", type=lambda s: int(s, 0), default=None)
    a = ap.parse_args()
    for p in a.kos:
        disasm_func(p, a.symbol, a.lo, a.hi)
        print()
