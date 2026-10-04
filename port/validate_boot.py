#!/usr/bin/env python3
"""校验 archytas-wifi-boot 两份镜像的正确性（不依赖实体设备）。

检查项：
  1. 文件尺寸 == 32 MiB 且 magic == ANDROID!
  2. page_size == 2048 且 cmdline 含 bootopt=64S3,32S1,64S1
  3. 内核 gzip 解压后含 "Linux version 4.9.117"
  4. 内核区尾部 DTB：Stage1 应保持 4G 模板 DTB；Stage2 必须是 **经
     patch_wifi_dtb.py 修补过的** Wi-Fi DTB —— 即含 `shared-dma-pool` 与
     `memory-region`（Gen4M 驱动需要 reserved-memory DMA pool，见
     ARCHYTAS_PORT.md 9.7），同时 `aw87329_pa` 仍在、充电参数仍是 Wi-Fi 板的。
"""
from __future__ import annotations

import hashlib
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOT_DIR = ROOT / "archytas-wifi-boot"
WIFI_DTB = ROOT / "port" / "wifi_stock.dtb"
WIFI_DTB_PATCHED = ROOT / "port" / "wifi_archytas.dtb"
TEMPLATE = ROOT / "archimedes-kernel-64-main" / "tools" / "archimedes-boot-template-32MiB.img"
# HANDOFF 记录的 archimedes 模板(4G) DTB SHA256 前缀
TEMPLATE_DTB_SHA = "7a5af1887a2ad6bdf2fb3e27d806a7d3914c2f7ba93fa781f3540b9ff77bf304"

PAGE = 2048
BOOT_SIZE = 32 * 1024 * 1024
FDT_MAGIC = b"\xd0\x0d\xfe\xed"
EXPECT_CMD = b"bootopt=64S3,32S1,64S1"


def u32(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


def trailing_fdt(blob: bytes) -> tuple[int, int]:
    pos = blob.rfind(FDT_MAGIC)
    while pos >= 0:
        if pos + 8 <= len(blob):
            size = struct.unpack_from(">I", blob, pos + 4)[0]
            if size >= 40 and pos + size == len(blob):
                return pos, size
        pos = blob.rfind(FDT_MAGIC, 0, pos)
    raise ValueError("no trailing FDT in kernel region")


def kernel_version(gzip_bytes: bytes) -> str:
    try:
        raw = zlib.decompress(gzip_bytes, 16 + zlib.MAX_WBITS)
    except Exception:
        do = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = do.decompress(gzip_bytes) + do.flush()
    idx = raw.find(b"Linux version")
    if idx < 0:
        return "(未找到)"
    end = raw.find(b"\x00", idx)
    end = end if end > idx else idx + 80
    return raw[idx:end].decode("ascii", "replace").strip()


def parse_image(path: Path) -> dict:
    d = path.read_bytes()
    assert len(d) == BOOT_SIZE, f"{path.name}: 尺寸 {len(d)} != 32MiB"
    assert d[:8] == b"ANDROID!", f"{path.name}: 非 Android boot 镜像"
    page = u32(d, 0x24) or 2048
    assert page == PAGE, f"{path.name}: page_size={page} != 2048"
    ks = u32(d, 8)
    rs = u32(d, 16)
    cmdline = d[0x40:0x40 + 512].rstrip(b"\x00").decode("ascii", "replace")
    region = d[PAGE:PAGE + ks]
    gz = region[: region.rfind(FDT_MAGIC)] if FDT_MAGIC in region else region
    off, size = trailing_fdt(region)
    dtb = region[off:off + size]
    return dict(
        path=path, page=page, ks=ks, rs=rs, cmdline=cmdline,
        kernel_ver=kernel_version(gz), dtb=dtb, dtb_sha=hashlib.sha256(dtb).hexdigest(),
        dtb_has_wifi=b"aw87329_pa" in dtb,
        # The Gen4M WLAN core aborts without these two: see ARCHYTAS_PORT.md 9.7.
        dtb_has_pool=b"shared-dma-pool" in dtb,
        dtb_has_memregion=b"memory-region" in dtb,
        dtb_ac_charger=dtb.count(struct.pack(">I", 0x10C8E0)),
    )


def main() -> None:
    # Optional arguments: image paths to check INSTEAD of the two canonical ones
    # in archytas-wifi-boot/. Point them at a freshly unpacked CI artifact before
    # flashing it -- the canonical files may be from an older build, and nothing
    # says so on the filename.
    #   python3 port/validate_boot.py _ci/<run>/boot-archytas-wifi*.img
    targets = [Path(a) for a in sys.argv[1:]] or [
        BOOT_DIR / "boot-archytas-wifi-stage1.img",
        BOOT_DIR / "boot-archytas-wifi.img",
    ]

    wifi_ref = WIFI_DTB.read_bytes() if WIFI_DTB.exists() else b""
    wifi_sha = hashlib.sha256(wifi_ref).hexdigest() if wifi_ref else "(无参考文件)"

    # Stage2 must carry the PATCHED DTB (reserved-memory DMA pool + memory-region
    # on wifi@18000000). Generate the expected bytes from the ROM DTB so the check
    # cannot silently pass against a stale on-disk copy.
    patched = b""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import patch_wifi_dtb
        if wifi_ref:
            patched, _ = patch_wifi_dtb.patch(wifi_ref)
        elif WIFI_DTB_PATCHED.exists():
            patched = WIFI_DTB_PATCHED.read_bytes()
    except Exception as e:
        print(f"[警告] 无法生成修补后的参考 DTB: {e}\n")
    patched_sha = hashlib.sha256(patched).hexdigest() if patched else "(无)"
    if patched:
        print(f"[基准] 修补后(期望) Wi-Fi DTB SHA256: {patched_sha}\n")

    # 模板(4G) DTB：优先用本地上游模板抽取，否则回退到 HANDOFF 记录的 SHA
    tpl_dtb_sha = TEMPLATE_DTB_SHA
    if TEMPLATE.exists():
        try:
            td = TEMPLATE.read_bytes()
            tks = u32(td, 8)
            tregion = td[PAGE:PAGE + tks]
            toff, tsize = trailing_fdt(tregion)
            tpl_dtb_sha = hashlib.sha256(tregion[toff:toff + tsize]).hexdigest()
            print(f"[基准] 从上游模板抽取 4G DTB SHA256: {tpl_dtb_sha}\n")
        except Exception as e:
            print(f"[警告] 无法从模板抽取 DTB({e})，改用 HANDOFF 记录 SHA\n")

    ok = True
    for p in targets:
        name = str(p)
        if not p.exists():
            print(f"[缺失] {name}")
            ok = False
            continue
        try:
            r = parse_image(p)
        except Exception as e:
            print(f"[解析失败] {name}: {e}")
            ok = False
            continue
        # Stage1 carries the 4G template DTB, Stage2 the Wi-Fi one. Decide by the
        # DTB itself rather than by the filename, so an arbitrary path works.
        is_stage1 = ("stage1" in name) or (r["dtb_sha"] == tpl_dtb_sha)
        stage = "Stage1(4G 模板)" if is_stage1 else "Stage2(Wi-Fi DTB)"
        cmd_ok = EXPECT_CMD in r["cmdline"].encode()
        pool_ok = r["dtb_has_pool"] and r["dtb_has_memregion"]
        if is_stage1:
            dtb_ok = (r["dtb_sha"] == tpl_dtb_sha)
            dtb_msg = f"== 模板(4G) DTB: {dtb_ok}"
        else:
            dtb_ok = (r["dtb_sha"] == patched_sha) if patched else False
            dtb_msg = f"== 修补后 Wi-Fi DTB: {dtb_ok}"
            if wifi_ref and r["dtb_sha"] == wifi_sha:
                dtb_msg += "  <<< 这是未修补的 ROM DTB！Gen4M 驱动仍会 -ENODEV"
        ver_ok = r["kernel_ver"].startswith("Linux version 4.9.117")
        img_ok = (r["page"] == PAGE and len(p.read_bytes()) == BOOT_SIZE)
        all_ok = cmd_ok and dtb_ok and ver_ok and img_ok and (pool_ok or is_stage1)
        ok = ok and all_ok

        print(f"===== {name}  ({stage}) =====")
        print(f"  镜像格式 32MiB/v1/2048页: {img_ok}")
        print(f"  cmdline含bootopt: {cmd_ok}")
        print(f"  内核版本 4.9.117: {ver_ok}  -> {r['kernel_ver']}")
        print(f"  尾部DTB {dtb_msg}")
        print(f"  尾部DTB SHA256: {r['dtb_sha']}")
        if not is_stage1:
            print(f"  reserved-memory DMA pool(shared-dma-pool): {r['dtb_has_pool']}")
            print(f"  wifi@18000000 有 memory-region: {r['dtb_has_memregion']}")
            print(f"  aw87329_pa 锚点仍在: {r['dtb_has_wifi']}")
            print(f"  Wi-Fi 板充电参数(0x10c8e0)出现次数: {r['dtb_ac_charger']} (应 ≥1)")
        print(f"  >>> 综合判定: {'PASS' if all_ok else 'FAIL'}\n")

    print(f"参考 wifi_stock.dtb SHA256: {wifi_sha}")
    print(f"参考 模板(4G) DTB SHA256: {tpl_dtb_sha}")
    print(f"\n==== 总判定: {'ALL PASS' if ok else '存在失败项，需排查'} ====")


if __name__ == "__main__":
    main()
