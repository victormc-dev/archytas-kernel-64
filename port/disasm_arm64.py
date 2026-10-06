#!/usr/bin/env python3
"""Minimal AArch64 linear disassembler -- enough to read one kernel function.

AArch64 is a fixed-width ISA (every instruction is exactly 4 bytes), so a
function can be walked by simply stepping 4 bytes at a time; no length
decoding is needed.  Only branch/call/return/ADR forms are annotated, which
is all that is required to see structural differences between two builds of
the same function.

Used here to settle whether build_connectivity_modules.sh step 2c-3 (delete
the FB_BLANK_POWERDOWN schedule_work() call in wmt_fb_notifier_callback) is
actually present in a given wmt_drv.ko.  The relocation table cannot answer
it alone: GCC's tail-merge folds the two identical `schedule_work()` blocks
(UNBLANK + POWERDOWN) into one, so `queue_work_on` shows ONE call site in a
pristine build too.  The control flow is what differs.
"""

import struct
import sys

import audit_wmt_fb_workaround as A


def sx(v, bits):
    """Sign-extend v from `bits`."""
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def annotate(insn, off, relocs):
    """Return (mnemonic, note) for one 32-bit instruction."""
    if insn == 0xD65F03C0:
        return "RET", ""
    if insn == 0xD503201F:
        return "NOP", ""
    top = insn & 0xFF000000
    # B / BL  -- imm26
    if insn & 0x7C000000 == 0x14000000:
        imm = sx(insn & 0x03FFFFFF, 26) << 2
        tgt = off + imm
        is_bl = (insn & 0x80000000) != 0
        return ("BL" if is_bl else "B"), "-> %#x" % tgt
    # B.cond -- imm19
    if top == 0x54000000:
        imm = sx((insn >> 5) & 0x7FFFF, 19) << 2
        return "B.cond", "-> %#x (cond=%d)" % (off + imm, insn & 0xF)
    # CBZ / CBNZ (32/64 bit)
    if insn & 0x7E000000 == 0x34000000:
        imm = sx((insn >> 5) & 0x7FFFF, 19) << 2
        return ("CBNZ" if insn & 0x01000000 else "CBZ"), "-> %#x" % (off + imm)
    # TBZ / TBNZ
    if insn & 0x7E000000 == 0x36000000:
        imm = sx((insn >> 5) & 0x3FFF, 14) << 2
        return ("TBNZ" if insn & 0x01000000 else "TBZ"), "-> %#x" % (off + imm)
    # ADRP
    if insn & 0x9F000000 == 0x90000000:
        return "ADRP", ""
    # ADD (immediate)
    if insn & 0xFF800000 == 0x91000000:
        return "ADD", ""
    # MOVZ / MOVK / MOVN
    if insn & 0x7F800000 in (0x52800000, 0x72800000, 0x12800000):
        return "MOV*", ""
    # LDR/STR family (rough)
    if insn & 0x3B000000 == 0x39000000 or insn & 0x3B000000 == 0x38000000:
        return "LD/ST", ""
    return "", ""


def disasm_func(path, fname, limit=None):
    e = A.ElfRel(path)
    sym = e.get(fname)
    if sym is None:
        print("  %s: not found" % fname)
        return
    shndx, val, size, *_ = sym
    sec = [s for s in e.secs if s["idx"] == shndx][0]
    base = sec["offset"] + val
    # relocation map: r_offset -> symbol name (within this section)
    relmap = {}
    for r_off, sname, rtype, _ad in e.relas_for(shndx):
        relmap.setdefault(r_off, (sname, rtype))

    print("  %s  [%#x..%#x)  %d bytes  (%s)" % (
        fname, val, val + size, size, path.split("/")[-1]))
    n = size // 4
    if limit:
        n = min(n, limit)
    prev_was_ret = False
    for k in range(n):
        off = val + k * 4
        insn = struct.unpack_from("<I", e.d, base + k * 4)[0]
        mn, note = annotate(insn, off, relmap)
        rel = ""
        if off in relmap:
            sname, rtype = relmap[off]
            if sname:
                if mn == "BL":
                    rel = "   *** CALL %s ***" % sname
                else:
                    rel = "   [reloc: %s]" % sname
        # mark a return boundary so branch targets are readable
        print("    %#08x: %08x  %-8s %-22s%s" % (off, insn, mn, note, rel))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    func = sys.argv[1]
    for p in sys.argv[2:]:
        disasm_func(p, func)
        print()
