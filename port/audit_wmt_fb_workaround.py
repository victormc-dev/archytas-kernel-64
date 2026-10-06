#!/usr/bin/env python3
"""Is the 2c-3 workaround (skip screen-off early POWERDOWN) baked into a
wmt_drv.ko?  Decided from the POWERDOWN arm's call target.

WHY neither strings nor relocation counting can answer this
----------------------------------------------------------
build_connectivity_modules.sh step 2c-3 deletes exactly ONE source line:

    case FB_BLANK_POWERDOWN:
        ...
        WMT_WARN_FUNC("@@@@@@@@@@wmt enter early POWERDOWN @@@@@@@@@@@@@@\\n");
        schedule_work(&gPwrOnOffWork);      <-- deleted, replaced by a comment
        break;

That leaves behind:

  * the pr_info format string "@@@@@@@@@@wmt enter early POWERDOWN", so
    `grep` finds the anchor in patched and pristine binaries alike;
  * the OTHER schedule_work() calls in the very same function
    (FB_BLANK_UNBLANK branch) and in wmt_dev_apo_ctrl(), so
    "queue_work_on is UNDEF" is true either way.

Counting `queue_work_on` relocations fails too: schedule_work() is an inline
wrapper, and GCC's tail-merge folds the two IDENTICAL `schedule_work(); break;`
arms (UNBLANK and POWERDOWN) into a single shared block, so even a pristine
build shows only ONE call site.  (Verified: agui's known-pristine 4G build and
our pristine zip#1 both show exactly one, and it is the same one.)

What actually changes is the first CALL inside the POWERDOWN arm.  The switch
compiles to `cmp wN,#0; b.eq unblank` / `cmp wN,#4; b.ne default`, so the arm
head is the `cmp wN,#4` that is immediately followed by `b.ne`.  Its first BL:

    pristine : bl queue_work_on      (the schedule_work() call)
    patched  : bl osal_warn_print    (only the WMT_WARN_FUNC log remains,
                                      then an unconditional branch to the
                                      epilogue / `mov w0,#0; ret`)

Cross-check: agui's 4G reference build (documented as carrying zero
workarounds) is reported PRISTINE by this rule, which is what makes it
trustworthy.
"""

import struct
import sys

STT_FUNC, STT_OBJECT = 2, 1
SHT_RELA = 4

# R_AARCH64_* relocation types we care to name
REL_NAMES = {
    275: "ADR_PREL_PG_HI21",
    277: "ADD_ABS_LO12_NC",
    282: "JUMP26",
    283: "CALL26",
}


class ElfRel:
    """ELF64 reader with full section headers, symbols and RELA sections."""

    def __init__(self, path):
        self.path = path
        self.d = open(path, "rb").read()
        if self.d[:4] != b"\x7fELF":
            raise SystemExit("%s: not an ELF file" % path)
        if self.d[4] != 2:
            raise SystemExit("%s: only ELF64 is handled" % path)
        (self.e_shoff,) = struct.unpack_from("<Q", self.d, 0x28)
        (self.e_shentsize,) = struct.unpack_from("<H", self.d, 0x3A)
        (self.e_shnum,) = struct.unpack_from("<H", self.d, 0x3C)
        (self.e_shstrndx,) = struct.unpack_from("<H", self.d, 0x3E)
        self.secs = [self._sh(i) for i in range(self.e_shnum)]
        self.secnames = self._sec_names()
        # (name -> (shndx, value, size, type, bind, defined)) for lookups ...
        self.syms = {}
        # ... and per-symtab index tables for relocation symbol resolution.
        # A rela's r_info index is relative to the symtab it links via sh_link,
        # so the tables must not be merged into one running counter.
        self.symtabs = {}
        self._read_symbols()

    # -- section headers -------------------------------------------------
    def _sh(self, i):
        off = self.e_shoff + i * self.e_shentsize
        (sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size,
         sh_link, sh_info, sh_addralign, sh_entsize) = struct.unpack_from(
            "<IIQQQQIIQQ", self.d, off)
        return dict(idx=i, name_off=sh_name, type=sh_type, flags=sh_flags,
                    addr=sh_addr, offset=sh_offset, size=sh_size, link=sh_link,
                    info=sh_info, align=sh_addralign, entsize=sh_entsize)

    def _sec_names(self):
        """Keep the raw .shstrtab blob.

        A plain `blob.split(b"\\x00")` keyed by running offset only covers the
        START of each NUL-terminated run.  Section header string tables rely on
        suffix sharing, so e.g. sh_name=32 legitimately points at ".text" which
        lives INSIDE the ".rela.text" run (starts at 27).  Looking such an
        offset up in a start-offset dict returns "" and every section loses its
        name.  Resolve on demand from the blob instead.
        """
        sh = self.secs[self.e_shstrndx]
        return self.d[sh["offset"]:sh["offset"] + sh["size"]]

    def secname(self, i):
        off = self.secs[i]["name_off"]
        blob = self.secnames
        if off <= 0 or off >= len(blob):
            return ""
        end = blob.find(b"\x00", off)
        if end < 0:
            end = len(blob)
        return blob[off:end].decode("utf-8", "replace")

    # -- symbols ---------------------------------------------------------
    def _name_at(self, base, size, idx):
        start = base + idx
        if start >= base + size:
            return ""
        end = self.d.find(b"\x00", start, base + size)
        if end < 0:
            return ""
        return self.d[start:end].decode("utf-8", "replace")

    def _read_symbols(self):
        for sh in self.secs:
            if self.secname(sh["idx"]) not in (".symtab", ".dynsym"):
                continue
            strtab = self.secs[sh["link"]]
            base, size = strtab["offset"], strtab["size"]
            n = sh["size"] // (sh["entsize"] or 24)
            table = []
            for k in range(n):
                off = sh["offset"] + k * sh["entsize"]
                st_name, st_info, _o, st_shndx, st_value, st_size = \
                    struct.unpack_from("<IBBHQQ", self.d, off)
                nm = self._name_at(base, size, st_name) if st_name else ""
                table.append((nm, st_shndx, st_value, st_size))
                if not nm:
                    continue
                stype, bind = st_info & 0xF, st_info >> 4
                defined = st_shndx != 0
                prev = self.syms.get(nm)
                if prev is None or (defined and not prev[5]):
                    self.syms[nm] = (st_shndx, st_value, st_size, stype, bind, defined)
            self.symtabs[sh["idx"]] = table

    def get(self, name):
        return self.syms.get(name)

    # -- relocations -----------------------------------------------------
    def relas(self):
        """[(target_shndx, [(r_offset, sym_name, r_type, addend), ...])]"""
        out = []
        for sh in self.secs:
            if sh["type"] != SHT_RELA:
                continue
            table = self.symtabs.get(sh["link"], [])
            n = sh["size"] // (sh["entsize"] or 24)
            entries = []
            for k in range(n):
                off = sh["offset"] + k * sh["entsize"]
                r_offset, r_info, r_addend = struct.unpack_from("<QQq", self.d, off)
                # ELF64 packs the symbol index HIGH and the type LOW -- the
                # opposite of ELF32.  Swapping them silently yields garbage
                # symbol names (usually empty) and zero matching call sites.
                sym_idx = r_info >> 32
                r_type = r_info & 0xFFFFFFFF
                sname = table[sym_idx][0] if sym_idx < len(table) else ""
                entries.append((r_offset, sname, r_type, r_addend))
            out.append((sh["info"], entries))
        return out

    def relas_for(self, target_shndx):
        for tgt, entries in self.relas():
            if tgt == target_shndx:
                return entries
        return []

    def enclosing_func(self, shndx, off):
        """Tightest DEFINED FUNC/NOTYPE symbol covering (shndx, off)."""
        best = None
        for nm, (s_shndx, val, sz, stype, _bind, defined) in self.syms.items():
            if not defined or s_shndx != shndx or sz == 0:
                continue
            if val <= off < val + sz:
                if best is None or sz < best[1]:
                    best = (nm, sz, val)
        return best


def _find_powerdown_branch(e, shndx, val, size):
    """Locate the FB_BLANK_POWERDOWN arm and the first call it makes.

    The compiler emits the switch as:
        cmp  wN, #FB_BLANK_UNBLANK(0) ; b.eq unblank
        cmp  wN, #FB_BLANK_POWERDOWN(4) ; b.ne default
    so `cmp wN, #4` followed by `b.ne` is the POWERDOWN arm head.  Its first
    `bl` is the whole ballgame:

        pristine : bl queue_work_on      (the schedule_work() call)
        patched  : bl osal_warn_print    (only the WMT_WARN_FUNC log remains)

    Relocation-count methods cannot see this because GCC tail-merges the two
    identical schedule_work() blocks into one, leaving a single call site in
    pristine builds too.  The call *inside the POWERDOWN arm* is what changes.
    """
    sec = [s for s in e.secs if s["idx"] == shndx][0]
    base = sec["offset"]
    relmap = {r_off: sname for r_off, sname, _t, _a in e.relas_for(shndx)}

    def insn_at(off):
        return struct.unpack_from("<I", e.d, base + off)[0]

    for k in range(size // 4 - 1):
        off = val + k * 4
        insn = insn_at(off)
        # subs wzr, wN, #imm12   (32-bit, S=1, Rd=31)
        if (insn & 0xFF800000) != 0x71000000 or (insn & 0x1F) != 31:
            continue
        if ((insn >> 10) & 0xFFF) != 4:
            continue
        nxt = insn_at(off + 4)
        # b.ne  (cond = 0b0001, low nibble)
        if (nxt & 0xFF000000) != 0x54000000 or (nxt & 0xF) != 1:
            continue
        arm = off + 8
        for j in range(0, 40):
            o = arm + j * 4
            i2 = insn_at(o)
            if (i2 & 0xFC000000) == 0x94000000:      # BL
                return off, o, relmap.get(o, "")
    return None


def report(path, label, func="wmt_fb_notifier_callback"):
    e = ElfRel(path)
    print("=" * 78)
    print("%s" % label)
    print("  %s  (%d bytes, %d syms)" % (
        path.split("/")[-1], len(e.d), len(e.syms)))
    print("-" * 78)
    tgt = e.get(func)
    if tgt is None:
        print("  %s: NOT FOUND in symbol table" % func)
        return None
    shndx, value, size, _st, _b, defined = tgt
    sec = e.secname(shndx) if shndx else "?"
    print("  %s: %s value=%#x size=%d" % (func, sec, value, size))

    hit = _find_powerdown_branch(e, shndx, value, size)
    if hit is None:
        print("  POWERDOWN arm (cmp wN,#4 + b.ne) NOT FOUND")
        return None
    cmp_off, bl_off, sym = hit
    print("  POWERDOWN arm: cmp@%#08x  first call @%#08x -> %s" % (
        cmp_off, bl_off, sym or "<no reloc>"))
    patched = sym != "queue_work_on"
    verdict = ("PATCHED  (2c-3 present -- POWERDOWN arm no longer schedules work)"
               if patched else
               "PRISTINE (2c-3 absent  -- POWERDOWN arm calls schedule_work)")
    print("  RESULT: %s" % verdict)
    print("  rule: first BL in the POWERDOWN arm == queue_work_on -> pristine" % ())
    return patched


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        print("usage: audit_wmt_fb_workaround.py <wmt_drv.ko> [more.ko ...]")
        return 2
    for p in argv[1:]:
        report(p, p)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
