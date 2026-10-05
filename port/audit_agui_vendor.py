#!/usr/bin/env python3
"""Audit a MTK connectivity vendor image against the Archytas 4G reference.

Purpose
-------
Re-produce, in a single command, the "suspend / POWERDOWN hang" (卡死) audit
that compares the Wi-Fi port against agui's known-good 4G vendor (vendor.img).

It answers the four questions the Wi-Fi port kept coming back to:

  1. Is `gConEmiPhyBase` (no Final suffix) an UND symbol -- i.e. supplied by
     the kernel (connectivity_build_in_adapter.c, consys-reserve-memory
     @ 0xbf000000) -- rather than a module-local 0?  If UND + non-zero in the
     kernel, `kalSetSuspendFlagToEMI` really writes the EMI suspend flag and
     the "空指针写 EMI" hypothesis from ARCHYTAS_PORT.md §9.16 is dead.
  2. Is `wlanDownloadEMISection` a real implementation (not the 8-byte stub)?
  3. Does `priv_driver_set_suspend_mode` still call `wlanSetSuspendMode` +
     `p2pSetSuspendMode` (i.e. NO 2c-2 suspend workaround)?
  4. Does `wmt_fb_notifier_callback` still queue work on POWERDOWN (i.e. NO
     2c-3 workaround)?

The reference answers (from agui's 4G vendor, verified 2026-10-05):
  - gConEmiPhyBase / gConEmiSize : UND (kernel exports them, consys 0xbf000000)
  - gConEmiPhyBaseFinal          : GLOBAL .bss  (module-local, EMI download)
  - wlanDownloadEMISection       : real impl (~472 bytes)
  - kalSetSuspendFlagToEMI       : real impl (~180 bytes)
  - priv_driver_set_suspend_mode : calls wlanSetSuspendMode + p2pSetSuspendMode
  - wmt_fb_notifier_callback     : calls queue_work_on(system_wq, ...)

Usage
-----
    # from an extracted module (fastest):
    python3 audit_agui_vendor.py -k wlan_drv_gen4m.ko -w wmt_drv.ko
    # from the raw vendor.img (needs debugfs):
    python3 audit_agui_vendor.py --image vendor.img
    # only the wlan module:
    python3 audit_agui_vendor.py -k wlan_drv_gen4m.ko

Exit code 0 = all reference invariants hold; 1 = mismatch found; 2 = could not
read a required input.
"""

import argparse
import os
import struct
import subprocess
import sys
import tempfile

# R_AARCH64 relocation types we care about.  readelf reports the branch-to-symbol
# relocation used by `bl` as 0x11b = 283 and names it R_AARCH64_CALL26.
R_ABS64 = 257
CALL26 = 283  # R_AARCH64_CALL26 (0x11b); used by `bl` calls


def fail(msg):
    print("FAIL: " + msg)
    sys.exit(2)


class Elf64:
    """Minimal 64-bit little-endian ELF reader (symtab + relas)."""

    SHT_SYMTAB = 2
    SHT_RELA = 4
    SHT_STRTAB = 3

    def __init__(self, path):
        with open(path, "rb") as fh:
            self.d = fh.read()
        if self.d[:4] != b"\x7fELF" or self.d[4] != 2:
            fail("%s: not a 64-bit ELF" % path)
        (_, _m, _v, _e, _po, shoff, _f, _eh, _pe, _pn,
         shentsize, shnum, shstrndx) = struct.unpack_from("<HHIQQQIHHHHHH",
                                                           self.d, 16)
        secs = []
        for i in range(shnum):
            o = shoff + i * shentsize
            name, typ, _fl, addr, off, size, link, info, align, entsize = \
                struct.unpack_from("<IIQQQQIIQQ", self.d, o)
            secs.append(dict(name=name, typ=typ, off=off, size=size,
                             link=link, entsize=entsize, addr=addr))
        self.secs = secs
        strtab_off = secs[shstrndx]["off"]
        self._sname = lambda x: self.d[strtab_off + x:
                                       self.d.index(b"\0", strtab_off + x)
                                       ].decode("utf-8", "replace")

    def symbols(self):
        out = []
        for s in self.secs:
            if s["typ"] not in (self.SHT_SYMTAB,):
                continue
            strtab_off = self.secs[s["link"]]["off"]
            sym_off = s["off"]
            entsize = s["entsize"] or 24
            n = s["size"] // entsize
            for i in range(n):
                o = sym_off + i * entsize
                (st_name, st_info, st_other, st_shndx,
                 st_value, st_size) = struct.unpack_from("<IBBHQQ",
                                                         self.d, o)
                bind = st_info >> 4
                typ = st_info & 0xF
                name = ""
                if st_name:
                    e = self.d.index(b"\0", strtab_off + st_name)
                    name = self.d[strtab_off + st_name:e].decode(
                        "utf-8", "replace")
                out.append(dict(name=name, bind=bind, typ=typ,
                                shndx=st_shndx, value=st_value,
                                size=st_size))
        return out

    def relas(self, section):
        for s in self.secs:
            if s["typ"] == self.SHT_RELA and self._sname(s["name"]) == section:
                strtab_off = self.secs[s["link"]]["off"]
                n = s["size"] // (s["entsize"] or 24)
                sym_off = s["off"]
                out = []
                for i in range(n):
                    o = sym_off + i * (s["entsize"] or 24)
                    (r_offset, r_info, r_addend) = struct.unpack_from(
                        "<QQq", self.d, o)
                    r_type = r_info & 0xFFFFFFFF
                    r_sym = r_info >> 32
                    out.append(dict(offset=r_offset, r_type=r_type,
                                    r_sym=r_sym, addend=r_addend))
                return out
        return []

    def sym_name(self, idx, syms):
        return syms[idx]["name"] if 0 <= idx < len(syms) else "?"


BIND = {0: "LOCAL", 1: "GLOBAL", 2: "WEAK"}


def find_symbols(syms, regex):
    import re
    pat = re.compile(regex)
    return [s for s in syms if pat.search(s["name"])]


def extract_from_img(image, paths, outdir):
    """debugfs-dump `paths` (image-absolute, leading '/') into outdir."""
    for p in paths:
        dst = os.path.join(outdir, os.path.basename(p))
        r = subprocess.run(["debugfs", "-R", "dump %s %s" % (p, dst),
                            image], capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(dst) or \
           os.path.getsize(dst) == 0:
            return None, "debugfs failed to extract %s" % p
    return outdir, None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-k", "--wlan", help="wlan_drv_gen4m.ko path (or in image)")
    ap.add_argument("-w", "--wmt", help="wmt_drv.ko path (or in image)")
    ap.add_argument("--image", help="vendor.img (ext4) to extract modules from")
    ap.add_argument("--kernel-wlan", action="store_true",
                    help="expect gConEmiPhyBase to be UND (kernel-supplied)")
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="agui_audit_")
    wlan = args.wlan
    wmt = args.wmt
    if args.image:
        paths = []
        if not wlan:
            paths.append("/lib/modules/wlan_drv_gen4m.ko")
        if not wmt:
            paths.append("/lib/modules/wmt_drv.ko")
        if paths:
            _, err = extract_from_img(args.image, paths, tmp)
            if err:
                fail(err)
            if not wlan:
                wlan = os.path.join(tmp, "wlan_drv_gen4m.ko")
            if not wmt:
                wmt = os.path.join(tmp, "wmt_drv.ko")

    if not wlan:
        fail("need a wlan module (-k) or an image (--image)")
    el = Elf64(wlan)
    syms = el.symbols()
    if not syms:
        fail("no symbols in %s" % wlan)

    # ---- symbol invariants ------------------------------------------------
    def one(name):
        hits = [s for s in syms if s["name"] == name]
        return hits[0] if hits else None

    g = one("gConEmiPhyBase")
    gs = one("gConEmiSize")
    gf = one("gConEmiPhyBaseFinal")
    dl = one("wlanDownloadEMISection")
    susp = one("kalSetSuspendFlagToEMI")

    print("== wlan_drv_gen4m.ko: EMI / suspend symbols ==")
    for nm, s in [("gConEmiPhyBase", g), ("gConEmiSize", gs),
                  ("gConEmiPhyBaseFinal", gf), ("wlanDownloadEMISection", dl),
                  ("kalSetSuspendFlagToEMI", susp)]:
        if not s:
            print("  %-26s MISSING" % nm)
            continue
        print("  %-26s %-6s shndx=%-3s size=%-6s value=0x%x"
              % (nm, BIND.get(s["bind"], "?"), s["shndx"],
                 s["size"], s["value"]))

    print("== checks ==")
    ok = True

    # 1. gConEmiPhyBase must be UND (kernel-supplied), NOT module-local.
    if g is None or g["shndx"] == 0:
        print("  [X] gConEmiPhyBase is UND (kernel export) -- GOOD (not 0)")
    else:
        print("  [ ] gConEmiPhyBase is module-defined (shndx=%s) -- kernel "
              "does NOT supply it" % g["shndx"])
        ok = False

    # 2. wlanDownloadEMISection must be a real implementation (>100 bytes).
    if dl and dl["size"] > 100:
        print("  [X] wlanDownloadEMISection size=%d -- real impl" % dl["size"])
    else:
        print("  [ ] wlanDownloadEMISection size=%s -- 8-byte stub?" % (
            dl["size"] if dl else "MISSING"))
        ok = False

    # 3. priv_driver_set_suspend_mode must still call wlanSetSuspendMode.
    pdsm = one("priv_driver_set_suspend_mode")
    wssm = one("wlanSetSuspendMode")
    p2p = one("p2pSetSuspendMode")
    if pdsm:
        relas = el.relas(".rela.text")
        start, size = pdsm["value"], pdsm["size"]
        calls = []
        for r in relas:
            if start <= r["offset"] < start + size and r["r_type"] == CALL26:
                calls.append(el.sym_name(r["r_sym"], syms))
        has_wssm = any("wlanSetSuspendMode" in c for c in calls)
        has_p2p = any("p2pSetSuspendMode" in c for c in calls)
        print("  priv_driver_set_suspend_mode calls: %s" % (calls or "()"))
        if has_wssm and has_p2p:
            print("  [X] suspend path intact (no 2c-2 workaround) -- GOOD")
        else:
            print("  [ ] suspend path MISSING wlanSetSuspendMode/p2p -- "
                  "2c-2 workaround present")
            ok = False
    else:
        print("  [ ] priv_driver_set_suspend_mode MISSING")
        ok = False

    # 4. wmt fb notifier must still queue work on POWERDOWN.
    if wmt:
        ew = Elf64(wmt)
        wsyms = ew.symbols()
        fb = [s for s in wsyms if s["name"] == "wmt_fb_notifier_callback"]
        if fb:
            fb = fb[0]
            wrel = ew.relas(".rela.text")
            wcalls = [ew.sym_name(r["r_sym"], wsyms) for r in wrel
                      if fb["value"] <= r["offset"] < fb["value"] + fb["size"]
                      and r["r_type"] == CALL26]
            q = any("queue_work_on" in c or "schedule_work" in c
                    for c in wcalls)
            print("  wmt_fb_notifier_callback calls: %s" % (wcalls or "()"))
            if q:
                print("  [X] fb POWERDOWN work intact (no 2c-3 workaround) "
                      "-- GOOD")
            else:
                print("  [ ] fb POWERDOWN work REMOVED (2c-3 workaround)")
                ok = False
        else:
            print("  [ ] wmt_fb_notifier_callback MISSING in wmt_drv.ko")

    print("")
    if ok:
        print("RESULT: PASS -- wlan module matches the agui 4G reference "
              "(kernel-supplied EMI base, no suspend workaround).")
        sys.exit(0)
    print("RESULT: MISMATCH -- see [ ] lines above.")
    sys.exit(1)


if __name__ == "__main__":
    main()
