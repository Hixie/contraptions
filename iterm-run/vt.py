"""Renders terminal output as the lines a terminal would show.

It understands only what zsh's line editor and the tests print: printable
characters with xterm's deferred line wrapping, carriage return, line
feed, backspace, and the escape sequences that move the cursor up or
right, home it, and erase. Other escape sequences, such as colours and
mode changes, are dropped."""

import re

ESCAPE = re.compile(r"\x1b\[([?0-9;]*)([A-Za-z])")


def screen(data, cols=80):
    rows, r, c = [[]], 0, 0
    text = data.decode(errors="replace")
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\x1b":
            m = ESCAPE.match(text, i)
            if m:
                param, cmd = m.groups()
                if cmd == "J" and param == "2":
                    rows, r, c = [[]], 0, 0
                elif cmd == "J":
                    del rows[r][c:]
                    del rows[r + 1:]
                elif cmd == "K":
                    del rows[r][c:]
                elif cmd == "H" and not param:
                    r, c = 0, 0
                elif cmd == "A":
                    r = max(0, r - int(param or 1))
                elif cmd == "C":
                    c += int(param or 1)
                i = m.end()
            else:
                i += 1
            continue
        if ch == "\r":
            c = 0
        elif ch == "\n":
            r += 1
        elif ch == "\b":
            c = max(0, c - 1)
        elif ch >= " ":
            if c >= cols:
                r, c = r + 1, 0
            while len(rows) <= r:
                rows.append([])
            row = rows[r]
            row.extend(" " * (c - len(row)))
            if c < len(row):
                row[c] = ch
            else:
                row.append(ch)
            c += 1
        while len(rows) <= r:
            rows.append([])
        i += 1
    return ["".join(row).rstrip() for row in rows]
