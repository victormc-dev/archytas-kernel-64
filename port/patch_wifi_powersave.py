#!/usr/bin/env python3
"""Keep the MTK Gen4M WLAN driver in CAM -- never power save.

Usage:
    python3 port/patch_wifi_powersave.py <wlan_src_dir>

Why this exists
---------------
Measured live on the device (2026-10-18), with the Magisk `service.d` daemon
that forced `iw dev wlan0 set power_save off` every 5 s REMOVED:

    svc wifi enable -> connect -> `iw dev wlan0 get power_save` == on
    ping 192.168.1.99: 30 sent, 29 received, 1 lost (3%)
                      min 7 ms, max 3084 ms, avg 598 ms

A kprobe on mtk_cfg80211_set_power_mgmt / mtk_cfg_set_power_mgmt (the symbols
registered in the cfg80211_ops) shows WHO turns it back on after every
reconnect:

    wpa_supplicant-16657 : apm_mtk_cfg_set_power_mgmt
        => mtk_cfg_set_power_mgmt
        => genl_family_rcv_msg / genl_rcv_msg / netlink_rcv_skb / genl_rcv
        => netlink_unicast / netlink_sendmsg / sock_sendmsg / ___sys_sendmsg
        => __sys_sendmsg / SyS_sendmsg
    wifi@1.0-servic-778  : apm_mtk_cfg80211_set_power_mgmt
        => cfg80211_netdev_notifier_call (NETDEV_UP, i.e. wlan0 going up)
        => raw_notifier_call_chain / __dev_notify_flags / dev_change_flags

i.e. Android's wpa_supplicant explicitly sends NL80211_CMD_SET_POWER_SAVE(enabled)
on every association, and the MTK WiFi HAL re-applies the stale wdev->ps value
when wlan0 comes up. The driver answers both with

    wlanoidSet802dot11PowerSaveProfile() -> nicConfigPowerSaveProfile()
    -> CMD_ID_POWER_SAVE_MODE(Param_PowerModeFast_PSP)

so the firmware sleeps between beacons. On this Wi-Fi-only teaching terminal
that is a hard bug: PC->device latency explodes (avg 598 ms, spikes to 3.1 s)
and wireless adb drops. The userspace daemon workaround only narrows the window
to 5 s -- it cannot stop the association-time reset.

The fix
-------
Make the cfg80211 .set_power_mgmt callback refuse any request to ENABLE power
save and push Param_PowerModeCAM instead:

  * the firmware is told CAM, so it never sleeps between beacons;
  * the callback returns -EOPNOTSUPP for enabled==true, so cfg80211's
    `wdev->ps` stays false and `iw dev wlan0 get power_save` reports "off"
    (nl80211_set_power_save() only stores the new value when the driver call
    succeeded);
  * enabled==false (the CAM direction) still works normally and returns 0.

Both the STA callback (mtk_cfg80211_set_power_mgmt in os/linux/gl_cfg80211.c)
and the P2P one (mtk_p2p_cfg80211_set_power_mgmt in
os/linux/gl_p2p_cfg80211.c) are patched, since either wiphy can be used.

Deliberately NOT done instead:
  * Dropping .set_power_mgmt from cfg80211_ops -- then nl80211 rejects
    SET_POWER_SAVE with -EOPNOTSUPP and never calls the driver, but the
    firmware's own default after CMD_ID_INDICATE_PM_BSS_CONNECTED is not
    guaranteed to be CAM. Actively sending CAM is deterministic.
  * Patching nicConfigPowerSaveProfile()/the OID instead -- that would also
    break the CTIA and TP-test-mode callers that legitimately select a PS
    profile. The cfg80211 callback is the one path Android actually uses
    (proven by the kprobe stacks above).

Idempotent -- safe to run on every build. The build script gates the shipped
.ko on the marker string being present in its .rodata, so a silent
patch-apply failure cannot reach the device.
"""
import sys
from pathlib import Path

MARKER = "ARCHYTAS_POWER_SAVE_OFF"

CAM_NOTE = (
    "\t\t/* " + MARKER + ": this Wi-Fi-only teaching terminal must stay in CAM.\n"
    "\t\t * See port/patch_wifi_powersave.py. Android's wpa_supplicant sends\n"
    "\t\t * NL80211_CMD_SET_POWER_SAVE(enabled) on every association and the MTK\n"
    "\t\t * WiFi HAL re-applies it when wlan0 comes up; honouring it puts the\n"
    "\t\t * firmware in Fast PSP, i.e. it sleeps between beacons. Measured with\n"
    "\t\t * PSP on: 3% loss, avg 598 ms, max 3084 ms -- wireless adb unusable.\n"
    "\t\t * Force CAM instead so the device is always reachable. */\n"
)

CAM_NOTE_P2P = CAM_NOTE.replace("\t\t", "\t")

COMMENT_STA = (
    "\t/* " + MARKER + ": refuse the enable request so cfg80211's wdev->ps stays\n"
    "\t * false and `iw dev wlan0 get power_save` reports off. The CAM profile\n"
    "\t * has already been pushed to the firmware above. The DBGLOG string is\n"
    "\t * deliberate: it is the only copy of the marker that reaches the\n"
    "\t * binary, and the build script gates the shipped .ko on finding it. */\n"
)

COMMENT_P2P = COMMENT_STA.replace("\t *", "\t *")

LOG_STA = (
    '\t\tDBGLOG(REQ, WARN,\n'
    '\t\t       "ARCHYTAS_POWER_SAVE_OFF: power save requested, forced CAM\\n");\n'
)

LOG_P2P = (
    '\t\tDBGLOG(REQ, WARN,\n'
    '\t\t       "ARCHYTAS_POWER_SAVE_OFF: power save requested, forced CAM\\n");\n'
)

REFUSE_STA = COMMENT_STA + "\tif (enabled) {\n" + LOG_STA + \
             "\t\treturn -EOPNOTSUPP;\n\t}\n\n"

REFUSE_P2P = COMMENT_P2P + "\tif (enabled) {\n" + LOG_P2P + \
             "\t\treturn -EOPNOTSUPP;\n\t}\n\n"


def _function_span(src, sig):
    """Return (start, end) of the function body whose signature contains `sig`.

    `end` is the index just past the function's closing brace. Braces inside
    string literals are not a hazard here: neither patched function contains a
    brace inside a literal.
    """
    i = src.find(sig)
    if i < 0:
        return None
    open_idx = src.find("{", i)
    if open_idx < 0:
        return None
    depth = 0
    j = open_idx
    while j < len(src):
        c = src[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i, j + 1
        j += 1
    return None


def _sub_once(text, old, new, what, where):
    if text.count(old) != 1:
        sys.exit("ERROR: %s: expected exactly 1 occurrence of %s, found %d -- "
                 "the wlan source layout changed"
                 % (where, what, text.count(old)))
    return text.replace(old, new, 1)


def patch_sta(path):
    src = path.read_text(encoding="utf-8", errors="surrogateescape")
    if MARKER in src:
        print("   already patched: %s" % path.name)
        return 0

    span = _function_span(src, "int mtk_cfg80211_set_power_mgmt(struct wiphy")
    if not span:
        sys.exit("ERROR: %s: mtk_cfg80211_set_power_mgmt() not found" % path)
    head, body, tail = src[:span[0]], src[span[0]:span[1]], src[span[1]:]

    body = _sub_once(
        body,
        "\t\tif (timeout == -1)\n"
        "\t\t\trPowerMode.ePowerMode = Param_PowerModeFast_PSP;\n"
        "\t\telse\n"
        "\t\t\trPowerMode.ePowerMode = Param_PowerModeMAX_PSP;",
        CAM_NOTE + "\t\trPowerMode.ePowerMode = Param_PowerModeCAM;",
        "the Fast/MAX PSP selection",
        "mtk_cfg80211_set_power_mgmt",
    )

    # The refusal goes in front of the function's FINAL `return 0;`. Anchor on
    # the error branch that precedes it so the earlier, early-return `return 0;`
    # (the fgEnDbgPowerMode escape hatch) is not touched.
    body = _sub_once(
        body,
        '\t\tDBGLOG(REQ, WARN, "set_power_mgmt error:%x\\n", rStatus);\n'
        "\t\treturn -EFAULT;\n"
        "\t}\n\n"
        "\treturn 0;\n"
        "}",
        '\t\tDBGLOG(REQ, WARN, "set_power_mgmt error:%x\\n", rStatus);\n'
        "\t\treturn -EFAULT;\n"
        "\t}\n\n"
        + REFUSE_STA +
        "\treturn 0;\n"
        "}",
        "the trailing `return 0;`",
        "mtk_cfg80211_set_power_mgmt",
    )

    path.write_text(head + body + tail, encoding="utf-8",
                    errors="surrogateescape")
    print("   patched %s: mtk_cfg80211_set_power_mgmt() now forces CAM and "
          "refuses enable" % path.name)
    return 1


def patch_p2p(path):
    src = path.read_text(encoding="utf-8", errors="surrogateescape")
    if MARKER in src:
        print("   already patched: %s" % path.name)
        return 0

    span = _function_span(
        src, "int mtk_p2p_cfg80211_set_power_mgmt(struct wiphy")
    if not span:
        sys.exit("ERROR: %s: mtk_p2p_cfg80211_set_power_mgmt() not found" % path)
    head, body, tail = src[:span[0]], src[span[0]:span[1]], src[span[1]:]

    body = _sub_once(
        body,
        "\tif (enabled)\n"
        "\t\tePowerMode = Param_PowerModeFast_PSP;\n"
        "\telse\n"
        "\t\tePowerMode = Param_PowerModeCAM;",
        CAM_NOTE_P2P + "\tePowerMode = Param_PowerModeCAM;",
        "the Fast PSP selection",
        "mtk_p2p_cfg80211_set_power_mgmt",
    )

    body = _sub_once(
        body,
        '\t\tDBGLOG(REQ, WARN, "set_power_mgmt error:%x\\n", rStatus);\n'
        "\t\treturn -EFAULT;\n"
        "\t}\n\n"
        "\treturn 0;\n"
        "}",
        '\t\tDBGLOG(REQ, WARN, "set_power_mgmt error:%x\\n", rStatus);\n'
        "\t\treturn -EFAULT;\n"
        "\t}\n\n"
        + REFUSE_P2P +
        "\treturn 0;\n"
        "}",
        "the trailing `return 0;`",
        "mtk_p2p_cfg80211_set_power_mgmt",
    )

    path.write_text(head + body + tail, encoding="utf-8",
                    errors="surrogateescape")
    print("   patched %s: mtk_p2p_cfg80211_set_power_mgmt() now forces CAM and "
          "refuses enable" % path.name)
    return 1


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: patch_wifi_powersave.py <wlan_src_dir>")
    root = Path(sys.argv[1])
    if not root.is_dir():
        sys.exit("ERROR: not a directory: %s" % root)

    sta = root / "os" / "linux" / "gl_cfg80211.c"
    p2p = root / "os" / "linux" / "gl_p2p_cfg80211.c"
    for p in (sta, p2p):
        if not p.is_file():
            sys.exit("ERROR: not found: %s" % p)

    n = patch_sta(sta) + patch_p2p(p2p)

    # Self-check: both markers must now be in the sources.
    for p in (sta, p2p):
        if MARKER not in p.read_text(encoding="utf-8", errors="surrogateescape"):
            sys.exit("ERROR: %s does not carry %s after patching" % (p, MARKER))
    print("   OK: %d file(s) patched, %s present in both" % (n, MARKER))
    return 0


if __name__ == "__main__":
    sys.exit(main())
