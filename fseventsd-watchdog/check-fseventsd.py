#!/usr/bin/env python3
"""Report whether this Mac's fseventsd has the sweep-cursor wraparound bug.

Disassembles the arm64e slice of fseventsd, the one Apple silicon Macs
run, with otool, and looks for the event table sweeper:
a function that refers to "event table is screwed up!" and frees records
with malloc_zone_free. In the affected builds it opens with
`cmn wN, #0x201` and `b.hi`, the compiled form of the wrapping
`end = cursor + 512; while (cursor < end)` loop that README.md describes.

Exits with status 3 if it finds that check, and 0 if it does not. Not
finding it does not prove the bug is fixed: the code may only have been
compiled differently.
"""

import re
import subprocess
import sys

FSEVENTSD = (
    "/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/"
    "FSEvents.framework/Versions/A/Support/fseventsd"
)

AFFECTED = 3

# The wrapping check comes before the sweeper's loop, a few instructions in.
PROLOGUE = 24


def functions(disassembly):
    """Splits otool -tV output into functions, which start with pacibsp."""
    current = []
    for line in disassembly.splitlines():
        if re.match(r"[0-9a-f]+\s+pacibsp\b", line) and current:
            yield current
            current = []
        current.append(line)
    if current:
        yield current


def find_wrapping_check(disassembly):
    """Returns the address of the affected sweeper, or None."""
    for lines in functions(disassembly):
        text = "\n".join(lines)
        if "event table is screwed up!" not in text or "_malloc_zone_free" not in text:
            continue
        prologue = lines[:PROLOGUE]
        for first, second in zip(prologue, prologue[1:]):
            if re.search(r"\scmn\s+w\d+, #0x201$", first) and re.search(r"\sb\.hi\s", second):
                return "0x" + lines[0].split()[0].lstrip("0")
    return None


def main():
    disassembly = subprocess.run(
        ["otool", "-arch", "arm64e", "-tV", FSEVENTSD], capture_output=True, text=True, check=True
    ).stdout
    address = find_wrapping_check(disassembly)
    if address:
        print(f"affected: the event table sweeper at {address} stops for good once its cursor reaches 0xFFFFFE00")
        return AFFECTED
    print("not found: fseventsd no longer has the wrapping cursor check this script looks for")
    return 0


if __name__ == "__main__":
    sys.exit(main())
