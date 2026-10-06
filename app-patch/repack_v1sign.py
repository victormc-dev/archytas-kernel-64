#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""repack_v1sign.py —— 替换 AndroidManifest.xml 并用 v1(JAR) 签名重新打包 APK。

为什么必须重签：原 APK 只有 v2/v3 签名（无 META-INF/*.SF），改动任何字节后
v2 摘要必然不匹配 → 安装被拒。本机没有 java/apksigner，故自行实现
APK Signature Scheme v1（JAR signing）：
    META-INF/MANIFEST.MF   每个条目的 SHA-256-Digest
    META-INF/CERT.SF       MANIFEST.MF 的 SHA-256-Digest-Manifest
    META-INF/CERT.RSA      CERT.SF 的 PKCS#7（openssl 生成）
Android 10 (API 29) 接受 v1-only；「必须 v2」是 API 30 + targetSdk>=30 才开始的。

对齐：本 APK 的 .so/.dex 全是 DEFLATE（无页对齐要求）；resources.arsc 是
STORED，需 data offset 4 字节对齐。新 manifest 用 STORED 存储且长度是 4 的
倍数，位移 Δ 因此天然是 4 的倍数 → 后续条目对齐关系保持不变。脚本会断言校验。
"""
from __future__ import annotations

import base64
import hashlib
import os
import struct
import subprocess
import sys
import zipfile
import zlib

ZIP_STORED = 0
ZIP_DEFLATED = 8

KEY = None
CERT = None


def crc32(b: bytes) -> int:
    return zlib.crc32(b) & 0xFFFFFFFF


def deflate(b: bytes, level: int = 9) -> bytes:
    co = zlib.compressobj(level, zlib.DEFLATED, -15)
    return co.compress(b) + co.flush()


def local_header_len(raw: bytes, off: int) -> tuple[int, int, int]:
    fnlen, extralen = struct.unpack_from("<HH", raw, off + 26)
    return 30 + fnlen + extralen, fnlen, extralen


def build_local_header(orig_lh: bytes, name: str, method: int,
                       crc: int, csize: int, usize: int,
                       extra_append: bytes = b"") -> bytes:
    """基于原 local header 重建（保留 filename/extra，可追加 extra，清 bit3）。"""
    fnlen, extralen = struct.unpack_from("<HH", orig_lh, 26)
    flags = struct.unpack_from("<H", orig_lh, 6)[0]
    flags &= ~0x08                       # 清除 data descriptor 位（我们直接给出 size/crc）
    oe = orig_lh[30 + fnlen:30 + fnlen + extralen]
    lh = bytearray(orig_lh[:30 + fnlen + extralen])
    struct.pack_into("<H", lh, 28, len(oe) + len(extra_append))   # extra 长度字段
    lh += extra_append
    struct.pack_into("<H", lh, 6, flags)
    struct.pack_into("<H", lh, 8, method)
    struct.pack_into("<I", lh, 14, crc)
    struct.pack_into("<I", lh, 18, csize)
    struct.pack_into("<I", lh, 22, usize)
    assert bytes(lh[30:30 + fnlen]) == name.encode(), "local header 文件名不一致"
    return bytes(lh)


def central_record(zi: zipfile.ZipInfo, offset: int, method: int,
                   crc: int, csize: int, usize: int, extra: bytes) -> bytes:
    name = zi.filename.encode()
    comment = zi.comment or b""
    rec = bytearray()
    rec += b"PK\x01\x02"
    rec += struct.pack("<H", (zi.create_system << 8) | zi.create_version)
    rec += struct.pack("<H", zi.extract_version)
    rec += struct.pack("<H", zi.flag_bits & ~0x08)
    rec += struct.pack("<H", method)
    rec += struct.pack("<HH", zi.date_time[3] << 11 | zi.date_time[4] << 5 | zi.date_time[5] // 2,
                       (zi.date_time[0] - 1980) << 9 | zi.date_time[1] << 5 | zi.date_time[2])
    rec += struct.pack("<III", crc, csize, usize)
    rec += struct.pack("<HHH", len(name), len(extra), len(comment))
    rec += struct.pack("<HH", 0, zi.internal_attr)
    rec += struct.pack("<I", zi.external_attr)
    rec += struct.pack("<I", offset)
    rec += name + extra + comment
    return bytes(rec)


# ------------------------------------------------------------------ 签名文件生成

def jar_line(s: str, width: int = 72) -> bytes:
    """按 JAR 规范折行：每行（含续行前导空格）不超过 width 字节，CRLF 结尾。"""
    b = s.encode("utf-8")
    out = bytearray()
    i = 0
    first = True
    while i < len(b):
        limit = width if first else width - 1      # 续行要占 1 字节前导空格
        chunk = b[i:i + limit]
        if not first:
            out += b" "
        out += chunk
        out += b"\r\n"
        i += len(chunk)
        first = False
    if not b:
        out += b"\r\n"
    return bytes(out)


def build_manifest_mf(entries: list[tuple[str, bytes]]) -> tuple[bytes, list]:
    """entries: [(name, content_sha256)] -> (MANIFEST.MF 字节, [(name, 该条目段的原始字节)])"""
    out = bytearray()
    out += jar_line("Manifest-Version: 1.0")
    out += jar_line("Created-By: 1.0 (Android SignApk)")
    out += b"\r\n"
    sections = []
    for name, digest in entries:
        d = base64.b64encode(digest).decode()
        sec = (jar_line("Name: " + name)
               + jar_line("SHA-256-Digest: " + d)
               + b"\r\n")
        sections.append((name, sec))
        out += sec
    return bytes(out), sections


def build_cert_sf(mf: bytes, sections: list) -> bytes:
    """CERT.SF：main attributes + **每个条目**的摘要。

    注意（JAR 规范的坑）：CERT.SF 里条目级的 SHA-256-Digest 是
    「MANIFEST.MF 中该条目那一段字节」的摘要，**不是**文件内容的摘要。
    jarsigner 会列出每个条目；只写 -Digest-Manifest 会让 Android 的
    验证器认为该条目无签名 → INSTALL_PARSE_FAILED_NO_CERTIFICATES。
    """
    out = bytearray()
    out += jar_line("Signature-Version: 1.0")
    out += jar_line("Created-By: 1.0 (Android SignApk)")
    out += jar_line("SHA-256-Digest-Manifest: "
                    + base64.b64encode(hashlib.sha256(mf).digest()).decode())
    out += b"\r\n"
    for name, sec in sections:
        out += jar_line("Name: " + name)
        out += jar_line("SHA-256-Digest: "
                        + base64.b64encode(hashlib.sha256(sec).digest()).decode())
        out += b"\r\n"
    return bytes(out)


def ensure_key(workdir: str) -> tuple[str, str]:
    key = os.path.join(workdir, "sign_key.pem")
    cert = os.path.join(workdir, "sign_cert.pem")
    if not (os.path.isfile(key) and os.path.isfile(cert)):
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048",
                        "-keyout", key, "-out", cert, "-days", "10950", "-nodes",
                        "-subj", "/CN=Archytas Impeller Patch/O=Archytas/C=CN"],
                       check=True, capture_output=True)
    return key, cert


def pkcs7_sign(sf: bytes, key: str, cert: str, out: str):
    p = subprocess.run(["openssl", "smime", "-sign",
                        "-in", "-", "-outform", "DER", "-out", out,
                        "-signer", cert, "-inkey", key,
                        "-binary", "-md", "sha256", "-nosmimecap"],
                       input=sf, capture_output=True)
    if p.returncode != 0:
        raise SystemExit("openssl 签名失败:\n" + p.stderr.decode("utf-8", "replace"))


# ------------------------------------------------------------------ 主流程

def main():
    apk_in, manifest_new, apk_out = sys.argv[1], sys.argv[2], sys.argv[3]
    workdir = os.path.dirname(os.path.abspath(apk_out))
    raw = open(apk_in, "rb").read()
    new_manifest = open(manifest_new, "rb").read()
    print("原 APK %d 字节；新 manifest %d 字节" % (len(raw), len(new_manifest)))
    assert len(new_manifest) % 4 == 0, "新 manifest 长度不是 4 的倍数，会破坏对齐"

    zin = zipfile.ZipFile(apk_in)
    infos = zin.infolist()
    # 每个条目的原始字节范围 = [header_offset, 下一个 header_offset)
    offs = sorted(zi.header_offset for zi in infos)
    nxt = {offs[i]: offs[i + 1] for i in range(len(offs) - 1)}
    bounds = {zi.filename: (zi.header_offset,
                            nxt.get(zi.header_offset, zin.start_dir)) for zi in infos}
    print("条目 %d；CD 起始 %d；签名块占用 %d 字节"
          % (len(infos), zin.start_dir, zin.start_dir - max(e for _, e in bounds.values())))

    # 要丢掉的旧签名文件
    def is_sig(n: str) -> bool:
        if not n.startswith("META-INF/"):
            return False
        base = n[len("META-INF/"):]
        return ("/" not in base) and base.upper().endswith((".SF", ".RSA", ".DSA", ".EC", ".MF"))

    dropped = [zi.filename for zi in infos if is_sig(zi.filename)]
    print("丢弃旧 v1 签名文件:", dropped or "(无)")

    # ---------- 第一遍：写 entries（不含签名文件） ----------
    fout = open(apk_out, "wb")
    pos = [0]

    def w(b: bytes):
        fout.write(b)
        pos[0] += len(b)

    records = []
    digest_entries = []
    processed = 0

    for zi in infos:
        name = zi.filename
        if is_sig(name):
            continue
        s, e = bounds[name]
        orig_lh_len, fnlen, extralen = local_header_len(raw, s)
        orig_block_len = e - s

        if name == "AndroidManifest.xml":
            orig_lh = raw[s:s + orig_lh_len]
            data = deflate(new_manifest, 9)          # 与原来一致用 DEFLATE（无需对齐）
            method = ZIP_DEFLATED
            crc = crc32(new_manifest)
            csize, usize = len(data), len(new_manifest)
            orig_extra = zi.extra
            # 追加 padding extra（id 0xd935 = Android zipalign 风格），使总位移 ≡0 (mod 4)
            n = ((-csize) % 4) + 4
            pad = struct.pack("<HH", 0xD935, n) + b"\x00" * n
            lh = build_local_header(orig_lh, name, method, crc, csize, usize, extra_append=pad)
            extra = orig_extra                        # CD 里的 extra 保持原样
            new_block_len = len(lh) + csize
            delta = new_block_len - orig_block_len
            print("  AndroidManifest.xml: %d -> %d 字节 (csize=%d, padding=%d, Δ=%d, Δ%%4=%d)"
                  % (orig_block_len, new_block_len, csize, len(pad), delta, delta % 4))
            assert delta % 4 == 0, "Δ 不是 4 的倍数"
        else:
            lh = raw[s:s + orig_lh_len]
            data = raw[s + orig_lh_len: s + orig_lh_len + zi.compress_size]
            method = zi.compress_type
            crc, csize, usize = zi.CRC, zi.compress_size, zi.file_size
            extra = zi.extra

        offset = pos[0]
        w(lh)
        w(data)
        records.append((zi, offset, method, crc, csize, usize, extra))

        content = data if method == ZIP_STORED else zlib.decompress(data, -15)
        assert len(content) == usize, (name, len(content), usize)
        assert crc32(content) == crc, "CRC 校验失败: " + name
        digest_entries.append((name, hashlib.sha256(content).digest()))
        del content
        processed += 1

    print("已写入 %d 个条目，entries 区 %d 字节" % (processed, pos[0]))

    # ---------- 生成签名文件 ----------
    key, cert = ensure_key(workdir)
    mf, sections = build_manifest_mf(digest_entries)
    sf = build_cert_sf(mf, sections)
    rsa_path = os.path.join(workdir, "CERT.RSA")
    pkcs7_sign(sf, key, cert, rsa_path)
    rsa = open(rsa_path, "rb").read()
    print("MANIFEST.MF %d B / CERT.SF %d B / CERT.RSA %d B / 摘要条目 %d"
          % (len(mf), len(sf), len(rsa), len(digest_entries)))

    # ---------- 追加签名条目 ----------
    import datetime
    dt = (2026, 10, 6, 12, 0, 0)
    for nm, content, method in (("META-INF/MANIFEST.MF", mf, ZIP_DEFLATED),
                                ("META-INF/CERT.SF", sf, ZIP_DEFLATED),
                                ("META-INF/CERT.RSA", rsa, ZIP_DEFLATED)):
        if method == ZIP_DEFLATED:
            data = deflate(content)
            csize, usize = len(data), len(content)
        else:
            data, csize, usize = content, len(content), len(content)
        crc = crc32(content)
        nb = nm.encode()
        lh = bytearray()
        lh += b"PK\x03\x04"
        lh += struct.pack("<HH", 20, 20)             # version needed, flags
        lh += struct.pack("<H", method)
        lh += struct.pack("<HH", 0, (2026 - 1980) << 9 | 10 << 5 | 6)   # time, date
        lh += struct.pack("<III", crc, csize, usize)
        lh += struct.pack("<H", len(nb))
        lh += struct.pack("<H", 0)          # extralen=0
        lh += nb
        offset = pos[0]
        w(bytes(lh))
        w(data)
        zi = zipfile.ZipInfo(nm, date_time=dt)
        zi.compress_type = method
        zi.create_system = 3
        zi.external_attr = 0o600 << 16
        records.append((zi, offset, method, crc, csize, usize, b""))

    # ---------- 中央目录 + EOCD ----------
    cd_off = pos[0]
    cd = bytearray()
    for zi, offset, method, crc, csize, usize, extra in records:
        cd += central_record(zi, offset, method, crc, csize, usize, extra)
    w(bytes(cd))
    n = len(records)
    w(b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, n, n, len(cd), cd_off, 0))
    fout.close()
    print()
    print("写出 %s：%d 字节（原 %d，%+d）" % (apk_out, pos[0], len(raw), pos[0] - len(raw)))

    # ---------- 自检 ----------
    print()
    print("=== 自检 ===")
    z2 = zipfile.ZipFile(apk_out)
    bad = z2.testzip()
    print("  testzip:", bad or "全部 OK")
    names = z2.namelist()
    print("  条目数: %d (原 %d)" % (len(names), len(infos)))
    for n2 in ("META-INF/MANIFEST.MF", "META-INF/CERT.SF", "META-INF/CERT.RSA"):
        print("  %-24s %s" % (n2, "OK" if n2 in names else "缺失 !!"))
    print("  新 manifest 读回 %d 字节, 与源一致: %s"
          % (len(z2.read("AndroidManifest.xml")), z2.read("AndroidManifest.xml") == new_manifest))
    # 对齐校验
    print("  对齐检查（STORED 条目 data offset %% 4）:")
    z3 = zipfile.ZipFile(apk_out)
    raw2 = open(apk_out, "rb").read()
    bad_align = []
    for zi in z3.infolist():
        if zi.compress_type != ZIP_STORED:
            continue
        lhl, fnl, el = local_header_len(raw2, zi.header_offset)
        doff = zi.header_offset + lhl
        if doff % 4 != 0:
            bad_align.append((zi.filename, doff % 4))
    print("    未对齐的 STORED 条目: %d 个" % len(bad_align))
    for b in bad_align[:10]:
        print("      %-40s %%4=%d" % b)
    hard = [b for b in bad_align if b[0] == "resources.arsc" or b[0].endswith(".so")]
    print("    其中硬要求(resources.arsc/.so): %d 个" % len(hard))
    if hard:
        raise SystemExit("!! resources.arsc 或 .so 未 4 字节对齐")
    print("  → 硬要求全部对齐 OK")


if __name__ == "__main__":
    main()
