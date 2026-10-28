Title: fseventsd leaks memory without bound after its event-table sweep cursor reaches 0xFFFFFE00

Area: macOS > File System Events (fseventsd)
Build: macOS 26.6.2 (25G83), Mac17,6, 128 GB. fseventsd 1413.160.2 (UUID 519A5391-0608-346B-A333-D34CC76E7EC4).

Summary

On a developer machine with heavy file churn, fseventsd grows without bound after a few days to a few weeks of uptime: 103 GB on 2026-10-22 and 62 GB on 2026-10-27, at about 1.4 to 1.8 MB per second, with CPU rising to about two cores. `footprint` attributes almost all of it to small heap allocations, and `spindump` shows the notify and disk-logger threads spinning in strncmp inside the shared event-lookup function. Restarting fseventsd fixes it until the next time.

Each episode starts at the only occurrence of this message in five days of recorded fseventsd logs:

    event table has grown too large. (filled 196608 / mask 0x3ffff)

Jetsam reports for the second episode: 12 MB at 2026-10-26 15:24, the message at 17:30:38, 57 GB at 02:37 the next day.

Cause, from the arm64e disassembly

Event records (0xC0 bytes from the default zone plus an interned path in PathStringZone) live in a chained hash table, the global at 0x100026830: buckets, mask, a too-large flag, filled-bucket count at +0x10, sweep cursor at +0x14. Releasing an event only decrements its refcount (0x100004540). Records are freed only by the incremental sweeper at 0x100004604, which the com.apple.fseventsd.notify thread calls after each batch and on its condition-variable timeout (0x100011204, 0x1000112e0).

The sweeper does the equivalent of:

    end = t->cursor + 512;           // uint32_t
    while (t->cursor < end) { sweep bucket (t->cursor & t->mask); t->cursor++; }

The compiler hoists this into `if (cursor >= 0xFFFFFE00) return;` (0x100004648: `cmn w8, #0x201; b.hi`). After about 8.39 million calls, the cursor reaches 0xFFFFFE00, `end` wraps, and the sweeper never runs or advances again. The only reset is the 64-bit store that zeroes filled and cursor when the table is resized (0x100005978). Resizing stops at 262,144 buckets (0x100005918: new size must be below 0x80000), where the message above is logged once. So once the table has reached its maximum size, a cursor stall is permanent:

- Records with refcount 0 are never freed, so every new distinct path leaks a record and its path.
- Chains grow without bound, so each lookup at 0x100005b50 walks more entries with strncmp. That is the CPU growth.

Below the maximum size, a stall causes a temporary leak until the table fills to 75%, resizes and resets the cursor. That ratchets the table towards its maximum size over a process's lifetime.

Suggested fix: make the cursor wrap correctly. For example, keep it modulo the table size, or count 512 iterations instead of comparing against `cursor + 512`.

Workaround: restart fseventsd when it logs "event table has grown too large".

This appears to be the same bug as FB25090184, whose reporter observed the cursor stopping at 0xFFFFFE00 in a running fseventsd (https://developer.apple.com/forums/thread/849819).
