#!/usr/bin/env python3
"""Stop the notification LED from strobing at ~17 Hz (PMIC ISINK blink clamp).

Usage:
    python3 port/patch_led_pmic_blink.py <kernel_src_dir>

Why this exists
---------------
The red notification LED (led@0, "mediatek,red") is routed by the stock MTK DTS
to the PMIC ISINK1 path: led_mode=0x3 (MT65XX_LED_MODE_PMIC) with
data=0x2 (MT65XX_LED_PMIC_NLED_ISINK1).  mt_mt65xx_blink_set() therefore calls
mt_led_blink_pmic(), whose hardware support is the 8-entry table

    pmic_period_array[] = { 2, 4, 6, 8, 10, 12, 20, 60 };   /* ms */
    pmic_freqsel_array[] = { 0, 1, 2, 3, 4, 5, 9, 28 };

find_time_index_pmic() returns the LAST index for any request above 60 ms, so
every ordinary notification blink (e.g. 300 ms on + 300 ms off) is silently
clamped to period 60 ms / freqsel 28 -- the PMIC ISINK then runs at
500/(28+1) ~= 16.7 Hz and the LED visibly strobes ("狂闪").  The register
cannot go slower (fsel is 5 bits), so the hardware fundamentally cannot do a
slow blink; this is MTK driver + stock DTS behaviour, identical on 4G and
Wi-Fi boards, not something the 64-bit port introduced.

The fix is to REFUSE the request instead of clamping it, in two places:

  1. mt_led_blink_pmic(): return -1 when on+off exceeds the hardware maximum
     (pmic_period_array[PMIC_PERIOD_NUM - 1]).
  2. mt_mt65xx_blink_set(): PROPAGATE that -1 instead of unconditionally
     returning 0 -- which used to make the LED core believe the (clamped)
     hardware blink was in effect.

The LED core already implements the fallback: drivers/leds/led-core.c's
led_blink_setup() calls led_set_software_blink() when blink_set() fails, so
the kernel timer blinks the LED at the REQUESTED period.

IMPORTANT: this changes the KERNEL, so it must be applied BEFORE the kernel is
compiled (see .github/workflows/build.yml), and the resulting boot image has
to be reflashed once. It is idempotent -- safe to run on every build.
"""
import re
import sys
from pathlib import Path

MARKER = "ARCHYTAS_LED_PMIC_BLINK"
TARGET_REL = Path("drivers/misc/mediatek/leds/mt6765/mtk_leds.c")

# --- 1) refuse out-of-range periods in mt_led_blink_pmic() -------------------
REFUSE_ANCHOR = re.compile(
    r"(if \(led->nled_mode != NLED_BLINK\)\n"
    r"[ \t]*return -1;\n)"
    r"\n"
    r"([ \t]*LEDS_DEBUG\(\"LED blink on time = %d offtime = %d\\n\",)")

REFUSE_CODE = (
    "\t/* " + MARKER + ": PMIC ISINK blinking only supports the 8 discrete\n"
    "\t * periods in pmic_period_array[] (max 60 ms); find_time_index_pmic()\n"
    "\t * clamped anything longer to the last entry (freqsel=28, ~16.7 Hz),\n"
    "\t * which made the notification LED strobe. Refuse the request so the\n"
    "\t * LED core falls back to a software timer blink at the requested\n"
    "\t * period. See port/patch_led_pmic_blink.py for the full rationale. */\n"
    "\tif ((led->blink_on_time + led->blink_off_time) >\n"
    "\t    pmic_period_array[PMIC_PERIOD_NUM - 1])\n"
    "\t\treturn -1;\n"
    "\n")

# --- 2) propagate the refusal in mt_mt65xx_blink_set() -----------------------
PROPAGATE_ANCHOR = re.compile(
    r"[ \t]*mt_led_blink_pmic\(led_data->cust\.data,\n"
    r"[ \t]*&nled_tmp_setting\);\n"
    r"[ \t]*return 0;\n")

PROPAGATE_CODE = (
    "\t\t\t\t/* " + MARKER + ": propagate the refusal so the LED core falls\n"
    "\t\t\t\t * back to a software blink. Returning 0 unconditionally here\n"
    "\t\t\t\t * used to make the framework believe the hardware blink\n"
    "\t\t\t\t * (clamped to ~16.7 Hz) was in effect. */\n"
    "\t\t\t\treturn mt_led_blink_pmic(led_data->cust.data,\n"
    "\t\t\t\t\t\t  &nled_tmp_setting);\n")


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__.split("Usage:")[1].split("\n")[0].strip()
                 if "Usage:" in __doc__ else "usage: patch_led_pmic_blink.py <kernel_src_dir>")
    target = Path(sys.argv[1]) / TARGET_REL
    if not target.is_file():
        sys.exit("ERROR: not found: %s" % target)

    src = target.read_text(encoding="utf-8", errors="surrogateescape")
    if MARKER in src:
        print("   already patched: %s" % target)
        return 0

    # The CI checkout is LF; a Windows-side copy of the tree may be CRLF.
    # Normalize for matching, restore the original convention on write.
    crlf = "\r\n" in src
    work = src.replace("\r\n", "\n") if crlf else src
    before = work

    m = REFUSE_ANCHOR.search(work)
    if not m:
        sys.exit("ERROR: could not find the mt_led_blink_pmic() nled_mode "
                 "guard in %s -- the kernel source layout changed" % target)
    work = work[:m.start()] + m.group(1) + "\n" + REFUSE_CODE + m.group(2) + work[m.end():]

    m = PROPAGATE_ANCHOR.search(work)
    if not m:
        sys.exit("ERROR: could not find the mt_led_blink_pmic() call site in "
                 "%s -- the kernel source layout changed" % target)
    work = work[:m.start()] + PROPAGATE_CODE + work[m.end():]

    out = work.replace("\n", "\r\n") if crlf else work
    target.write_text(out, encoding="utf-8", errors="surrogateescape")

    # --- self-check: both edits landed and the file is still balanced -------
    problems = []
    if "pmic_period_array[PMIC_PERIOD_NUM - 1]" not in work:
        problems.append("refuse check missing")
    if "return mt_led_blink_pmic(led_data->cust.data," not in work:
        problems.append("propagate site missing")
    if work.count("{") != before.count("{"):
        problems.append("brace imbalance")
    if work.count("return 0;") != before.count("return 0;") - 1:
        problems.append("return-0 count unexpected (%d -> %d)"
                        % (before.count("return 0;"), work.count("return 0;")))
    if problems:
        sys.exit("ERROR: patch verification failed: " + "; ".join(problems))

    print("   patched %s: refuse>60ms + propagate refusal "
          "(software blink fallback)" % target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
