#!/usr/bin/env python3
"""原厂对原厂：4G 版 vs Wi-Fi 版 设备树差异（主DTB + dtbo overlay）"""
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dtb_dump import dump
from dtb_diff import parse
ROOT = os.path.dirname(os.path.abspath(__file__))

def extract_appended(boot_path):
    data = open(boot_path, "rb").read()
    page = 2048
    ksize = struct.unpack("<I", data[8:12])[0]
    blob_region = data[page:page + ksize]
    pos = blob_region.rfind(b'\xd0\x0d\xfe\xed')
    tsize = struct.unpack(">I", blob_region[pos + 4:pos + 8])[0]
    return blob_region[pos:pos + tsize]

def extract_dtbo_frag(dtbo_path):
    data = open(dtbo_path, "rb").read()
    idx = data.find(b'\xd0\x0d\xfe\xed')
    tsize = struct.unpack(">I", data[idx + 4:idx + 8])[0]
    return data[idx:idx + tsize]

boot4 = extract_appended(ROOT + "/柴提全原无数据4G_9.1864/4G原包/boot.bin")
open(ROOT + "/port/4g_oem_boot.dtb", "wb").write(boot4)
dtbo4 = extract_dtbo_frag(ROOT + "/柴提全原无数据4G_9.1864/4G原包/dtbo.bin")
open(ROOT + "/port/4g_oem_dtbo.dtb", "wb").write(dtbo4)
print("[抽取] 4G boot主DTB=%d字节  4G dtbo overlay=%d字节" % (len(boot4), len(dtbo4)))

# -------- 主DTB 对比 --------
A = parse(dump(ROOT + "/port/4g_oem_boot.dtb"))[0]
B = parse(dump(ROOT + "/port/wifi_stock.dtb"))[0]
sa, sb = set(A), set(B)
common = sa & sb
print("\n===== 主DTB: 4G原厂 vs Wi-Fi原厂 =====")
print("节点数 4G=%d  WiFi=%d  共同=%d" % (len(sa), len(sb), len(common)))
print("仅4G有=%d  仅WiFi有=%d" % (len(sa - sb), len(sb - sa)))
status_diffs = [(p, A[p].get('status'), B[p].get('status')) for p in common if A[p].get('status') != B[p].get('status')]
print("status开关差异(%d):" % len(status_diffs))
for p, va, vb in status_diffs:
    print("   ", p, " 4G=%s WiFi=%s" % (va, vb))
for kind in ('compatible', 'reg'):
    diffs = [(p, A[p].get(kind), B[p].get(kind)) for p in common if A[p].get(kind) != B[p].get(kind)]
    print("%s差异(%d):" % (kind, len(diffs)))
    for p, va, vb in diffs[:12]:
        print("   ", p, "\n      4G:", va, "\n      WiFi:", vb)
for p in common:
    for k in ('charging_host_charger_current', 'ac_charger_input_current'):
        if A[p].get(k) != B[p].get(k):
            print("CHARGER", p, "|", k, "| 4G=", A[p].get(k), "WiFi=", B[p].get(k))

# -------- Overlay 对比 --------
C = parse(dump(ROOT + "/port/4g_oem_dtbo.dtb"))[0]
D = parse(dump(ROOT + "/port/dtbo_internal.dtb"))[0]
sc_set, sd_set = set(C), set(D)
print("\n===== Overlay: 4G原厂 vs Wi-Fi原厂 =====")
print("节点数 4G=%d  WiFi=%d  共同=%d" % (len(sc_set), len(sd_set), len(sc_set & sd_set)))
print("仅4G overlay有=%d  仅WiFi overlay有=%d" % (len(sc_set - sd_set), len(sd_set - sc_set)))
def board_devs(tree):
    s = set()
    for p in tree:
        segments = p.split('/')
        if len(segments) >= 4 and segments[1].startswith('fragment@') and segments[2] == '__overlay__':
            s.add(segments[3])
    return s
dc, dd = board_devs(C), board_devs(D)
print("4G板级设备节点:", sorted(dc))
print("WiFi板级设备节点:", sorted(dd))
print(">>> 仅4G有:", sorted(dc - dd))
print(">>> 仅WiFi有:", sorted(dd - dc))
ov_status = [(p, C[p].get('status'), D[p].get('status')) for p in (sc_set & sd_set) if C[p].get('status') != D[p].get('status')]
print("overlay status差异(%d):" % len(ov_status))
for p, va, vb in ov_status[:30]:
    print("   ", p, " 4G=%s WiFi=%s" % (va, vb))
