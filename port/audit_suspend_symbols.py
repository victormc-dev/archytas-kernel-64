#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Symbol-level audit of the connectivity modules, answering the question the
PR's audit doc raises: are the 2c-2 / 2c-3 workarounds still in our build?

Plain substring matching cannot answer it -- a stripped .ko still carries the
*names* of functions it no longer calls. So this parses the real ELF symbol
table (.symtab / .dynsym) and reports, per symbol:

  DEFINED  -> the module defines the function (it exists in our build)
  UNDEF    -> the module imports it (resolved elsewhere, e.g. the kernel)

For the suspend path that distinction is the whole point: a workaround that
replaces the body with `return 0` keeps the symbol DEFINED but drops the
callee, so we additionally look for the *callees* the real implementation
needs. agui's unstripped 4G modules act as the reference: their .symtab also
carries STT_FILE / STT_NOTYPE entries, so symbol TYPE is what separates a real
function from a leftover name.

Usage:
  audit_suspend_symbols.py <wlan.ko> <wmt.ko> [--label NAME]
  audit_suspend_symbols.py --ref  <wlan.ko> <wmt.ko>   # show sizes too
"""
import struct
import sys

# (symbol, why we care) -- the four invariants from AGUI_4G_VENDOR_AUDIT.md
WLAN_SYMBOLS = [
    ("priv_driver_set_suspend_mode", "2c-2 entry: driver ioctl for suspend"),
    ("wlanSetSuspendMode",            "2c-2 real impl (must be CALLED)"),
    ("p2pSetSuspendMode",             "2c-2 real impl (must be CALLED)"),
    ("kalSetSuspendFlagToEMI",        "writes the suspend flag into EMI"),
    ("wlanDownloadEMISection",        "real impl when MTK_ANDROID_EMI=y"),
    ("kalIoctlByBssIdx",              "the ioctl that timed out for 30.7 s"),
    ("gConEmiPhyBase",                "UND => kernel exports consys base"),
    ("gConEmiPhyBaseFinal",           "module .bss copy used by EMI download"),
]

WMT_SYMBOLS = [
    ("wmt_fb_notifier_callback", "fb POWERDOWN notifier"),
    ("queue_work_on",            "2c-3 deleted this deferral"),
    ("wmt_cdev_rstmsg_snd",      "reset message to the chrdev"),
]

STT_FUNC = 2
STT_OBJECT = 1
STB_LOCAL, STB_GLOBAL, STB_WEAK = 0, 1, 2


class Elf:
    """Just enough ELF64 to walk .symtab/.dynsym. Read-only, no deps."""

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
        self.syms = {}          # name -> (value, size, type, bind, defined?)
        self._sections = None
        self._read_symbols()

    def _sh(self, i):
        """Parse one Elf64_Shdr.

        Layout (Elf64_Shdr is 64 bytes):
          0x00 sh_name      0x08 sh_flags     0x10 sh_addr
          0x18 sh_offset    0x20 sh_size      0x28 sh_link
          0x2C sh_info      0x30 sh_addralign 0x38 sh_entsize
        Getting this order wrong silently yields nonsense (sh_entsize read as
        sh_info), so the whole struct is unpacked in one go.
        """
        off = self.e_shoff + i * self.e_shentsize
        (sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size,
         sh_link, sh_info, sh_addralign, sh_entsize) = struct.unpack_from(
            "<IIQQQQIIQQ", self.d, off)
        return dict(name_off=sh_name, type=sh_type, offset=sh_offset,
                    size=sh_size, link=sh_link, entsize=sh_entsize)

    def _sec_names(self):
        """[(name_offset, name), ...] for the section header string table."""
        if self._sections is not None:
            return self._sections
        sh = self._sh(self.e_shstrndx)
        blob = self.d[sh["offset"]:sh["offset"] + sh["size"]]
        names, pos = [], 0
        for part in blob.split(b"\x00"):
            names.append((pos, part.decode("utf-8", "replace")))
            pos += len(part) + 1
        self._sections = names
        return names

    def _name_at(self, strtab, idx):
        """Read a NUL-terminated name from the given .strtab section.

        strtab is (offset, size). The bound matters: a plain d.index(b"\\0", base+idx)
        can run past the end of this string table into the next section and return
        a truncated name (or a name from a different table entirely).
        """
        base, size = strtab
        start = base + idx
        if start >= base + size:
            return ""
        end = self.d.find(b"\x00", start, base + size)
        if end < 0:
            return ""
        return self.d[start:end].decode("utf-8", "replace")

    def _read_symbols(self):
        names = self._sec_names()
        for i in range(self.e_shnum):
            sh = self._sh(i)
            secname = ""
            for off, nm in names:
                if off == sh["name_off"]:
                    secname = nm
                    break
            if secname not in (".symtab", ".dynsym"):
                continue
            strtab = self._sh(sh["link"])
            strbase = (strtab["offset"], strtab["size"])
            n = sh["size"] // (sh["entsize"] or 24)
            for k in range(n):
                off = sh["offset"] + k * sh["entsize"]
                st_name, st_info, _other, st_shndx, st_value, st_size = \
                    struct.unpack_from("<IBBHQQ", self.d, off)
                if st_name == 0:
                    continue
                nm = self._name_at(strbase, st_name)
                if not nm:
                    continue
                stype, bind = st_info & 0xF, st_info >> 4
                # A symbol is "defined by this module" whenever it is bound to a
                # section (shndx != 0). Restricting to STT_FUNC/STT_OBJECT is too
                # narrow: in these modules the linker keeps plenty of entries as
                # STT_NOTYPE, and dropping those makes real functions look absent.
                defined = st_shndx != 0
                prev = self.syms.get(nm)
                # prefer a DEFINED entry over an UNDEF one
                if prev is None or (defined and not prev[4]):
                    self.syms[nm] = (st_value, st_size, stype, bind, defined)

    def get(self, name):
        return self.syms.get(name)


def report(label, wlan_path, wmt_path):
    w = Elf(wlan_path)
    m = Elf(wmt_path)
    print("=" * 74)
    print("%s" % label)
    print("  wlan: %s (%d syms)" % (wlan_path.split("/")[-1], len(w.syms)))
    print("  wmt : %s (%d syms)" % (wmt_path.split("/")[-1], len(m.syms)))
    print("-" * 74)
    print("  %-32s %-8s %-10s %s" % ("SYMBOL", "KIND", "SIZE", "WHY"))
    for name, why in WLAN_SYMBOLS:
        e = w.get(name)
        if e is None:
            kind, size = "ABSENT", "-"
        elif e[4]:
            kind, size = "DEFINED", str(e[1])
        else:
            kind, size = "UNDEF", "-"
        print("  %-32s %-8s %-10s %s" % (name, kind, size, why))
    for name, why in WMT_SYMBOLS:
        e = m.get(name)
        if e is None:
            kind, size = "ABSENT", "-"
        elif e[4]:
            kind, size = "DEFINED", str(e[1])
        else:
            kind, size = "UNDEF", "-"
        print("  %-32s %-8s %-10s %s" % (name, kind, size, why))
    # verdict helpers
    gs = w.get("gConEmiPhyBase")
    print("-" * 74)
    print("  gConEmiPhyBase      : %s" %
          ("UNDEF -> kernel exports it (GOOD, matches agui)" if gs and not gs[4]
           else "DEFINED in module (check!)" if gs else "ABSENT (check!)"))
    wsm = w.get("wlanSetSuspendMode")
    p2p = w.get("p2pSetSuspendMode")
    if wsm and wsm[1] == 0:
        print("  wlanSetSuspendMode  : size 0 -> looks like the 2c-2 `return 0` stub")
    elif wsm:
        print("  wlanSetSuspendMode  : size %d -> real implementation present" % wsm[1])
    if p2p and p2p[1] == 0:
        print("  p2pSetSuspendMode   : size 0 -> 2c-2 stub")
    qw = m.get("queue_work_on")
    print("  queue_work_on       : %s" %
          ("UNDEF (2c-3 removed its use?)" if qw and not qw[4] else
           "DEFINED" if qw else "ABSENT"))
    print()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    label = "modules"
    for a in sys.argv[1:]:
        if a.startswith("--label"):
            i = sys.argv.index(a)
            label = sys.argv[i + 1] if i + 1 < len(sys.argv) else label
    if len(args) < 2:
        sys.exit(__doc__)
    report(label, args[0], args[1])
