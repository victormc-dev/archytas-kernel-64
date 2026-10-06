#!/usr/bin/env python3
"""Decide whether scanGetCurrentEssChnlList's list-drain loop can terminate.

WHY THIS EXISTS
---------------
`svc wifi disable` hangs the whole Wi-Fi control plane on this port.  sysrq-NMI
backtraces pin the spinning PC inside scanGetCurrentEssChnlList, and the reason
is visible in the code: the "unlink every node then free" loop compiled to

    .Lloop:  LDP   x2, x1, [x0, #16]      ; x2 = n->next, x1 = n->prev
             CBZ   x2, 1f
             STR   x1, [x2, #8]
    1:       CBZ   x1, 2f
             STR   x2, [x1]
    2:       LDR   w1, [head, #16]        ; <-- counter load CLOBBERS x1
             STP   xzr, xzr, [x0, #16]
             SUB   w1, w1, #1
             STR   w1, [head, #16]
             B     .Lloop                 ; unconditional, x0 never advances

`STP xzr, xzr, [x0, #16]` zeroes the node that the next iteration re-reads, so
after one pass x2 == x1 == 0, both CBZ are taken, and the loop just keeps
decrementing the counter forever.  The instruction that would have reloaded the
walk pointer (`LDR x1, [head]` = list_first_entry again) is simply ABSENT.

The reference build (agui) has it, and the back-edge there is conditional:

    .Lloop:  SUB   x0, x1, x4            ; node = &n->next - offsetof(next)
             CBZ   x1, panic
             ADD   x0, x0, x3
             LDP   x2, x1, [x0, #16]
             ... unlink ...
             LDR   x1, [head]             ; <-- RELOAD
             STP   xzr, xzr, [x0, #16]
             LDR   w0, [head, #16]
             SUB   w0, w0, #1
             STR   w0, [head, #16]
             SUBS  xzr, x1, head
             B.NE  .Lloop                 ; CONDITIONAL

So the discriminator is mechanical:
  GOOD  <=> back-edge is B.cond to the loop head AND a 64-bit LDR (the reload)
            appears inside the loop body
  BROKEN<=> back-edge is an unconditional B, no 64-bit LDR reload in the body

Run:  python audit_scan_loop.py [ko ...]        (defaults to ../_ci/**/*.ko)
"""
import glob
import os
import struct
import sys

import audit_wmt_fb_workaround as A

FUNC = "scanGetCurrentEssChnlList"
LDP_X2_X1_16 = 0xA9410402   # LDP x2, x1, [x0, #16]


def _sx(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def analyze(path):
    e = A.ElfRel(path)
    sym = e.get(FUNC)
    if sym is None:
        return {"err": "symbol not found"}
    shndx, val, size = sym[0], sym[1], sym[2]
    sec = [s for s in e.secs if s["idx"] == shndx][0]
    base = sec["offset"] + val
    n = size // 4
    ins = [struct.unpack_from("<I", e.d, base + 4 * k)[0] for k in range(n)]

    for k in range(n):
        if ins[k] != LDP_X2_X1_16:
            continue
        # walk forward to the first control transfer = the back edge
        for j in range(k, min(n, k + 24)):
            v = ins[j]
            kind = None
            if v >> 24 == 0x54:                        # B.cond
                tgt = val + 4 * j + _sx((v >> 5) & 0x7FFFF, 19) * 4
                kind = "B.cond"
            elif (v & 0x7C000000) == 0x14000000:       # B / BL
                tgt = val + 4 * j + _sx(v & 0x3FFFFFF, 26) * 4
                kind = "BL" if v & 0x80000000 else "B"
            if kind:
                body = ins[k:j + 1]
                reloads = [x for x in body if (x & 0xFFC00000) == 0xF9400000]
                guard = [x for x in body if (x & 0xFF000000) == 0xB4000000]
                return {
                    "val": val, "size": size,
                    "loop_head": val + 4 * k,
                    "ldp_at": val + 4 * k,
                    "backedge_at": val + 4 * j,
                    "span_bytes": (j - k + 1) * 4,
                    "backedge": kind,
                    "target": tgt,
                    # The loop HEAD is the back-edge target, which may sit a few
                    # instructions *before* the LDP: a correct build puts the
                    # `CBZ p, panic` guard (and, for some shapes, the
                    # `x0 = p - off` recompute) ahead of the unlink block, and
                    # branches back to that guard, not to the LDP.
                    "target_is_head": (tgt < val + 4 * k
                                       and (val + 4 * k) - tgt <= 0x20),
                    "x_ldr_in_body": ["%08x" % x for x in reloads],
                    "cbz_guard_in_body": ["%08x" % x for x in guard],
                }
        return {"err": "LDP found but no control transfer after it"}
    return {"err": "LDP x2,x1,[x0,#16] pattern not found"}


def verdict(r):
    """GOOD/BROKEN for one analysed loop.

    GOOD requires BOTH:
      * a conditional back-edge to the loop head (the loop can leave), and
      * a 64-bit LDR reload inside the body -- that is LINK_PEEK_HEAD()'s
        re-read of `head->prNext`, without which the walk pointer is frozen
        and the drain cannot terminate.
    """
    if "err" in r:
        return "?"
    ok = (r["backedge"] == "B.cond" and r["target_is_head"]
          and len(r["x_ldr_in_body"]) > 0)
    return "GOOD" if ok else "BROKEN"


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        args = sorted(glob.glob(os.path.join(os.path.dirname(__file__),
                                             "..", "_ci", "**", "wlan_drv_gen4m.ko"),
                                recursive=True))
    bad = 0
    for p in args:
        tag = os.path.relpath(p, os.path.join(os.path.dirname(__file__), ".."))
        r = analyze(p)
        if "err" in r:
            print("%-64s %s" % (tag, r["err"]))
            bad += 1
            continue
        v = verdict(r)
        if v != "GOOD":
            bad += 1
        print("%-64s %s" % (tag, v))
        print("    func@%#x size=%d   ldp@%#x backedge@%#x (%d bytes)   %s -> %#x (head=%s)"
              % (r["val"], r["size"], r["ldp_at"], r["backedge_at"],
                 r["span_bytes"], r["backedge"], r["target"], r["target_is_head"]))
        print("    64-bit LDR in body: %s" % (", ".join(r["x_ldr_in_body"]) or "NONE <<<"))
    if len(args) == 1 and bad:
        raise SystemExit(1)
