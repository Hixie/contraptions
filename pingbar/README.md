# pingbar

A macOS menu bar indicator that shows recent ping times to 8.8.8.8 as a
3-by-3 grid of colored circles. Each circle is one ping sample. The grid is a
ring buffer: the newest sample overwrites the oldest, so the grid always
shows the last nine samples. Slot order runs left to right, then top to
bottom, and wraps around.

## What the colors mean

- **Gray**: no sample recorded in that slot yet (only seen right after launch).
- **Green**: a reply arrived in under 100 ms.
- **Orange**: a reply arrived, but took between 100 ms and 300 ms.
- **Red**: the reply took 300 ms or more, or no reply arrived at all.

When a new sample is green, the grid updates without any animation, so a
healthy connection is visually quiet. When a new sample is orange or red, the
circle pulses (starts slightly enlarged and shrinks to its normal size) as it
lands, so a degraded or dead connection draws attention on every update.

When five or more of the nine circles are red, a click-through red-black
vignette appears around every screen. Each additional red circle increases both
the vignette's opacity and how far it reaches into the screen. The center stays
clear, and the effect disappears as soon as the number of red circles falls
back to four.

Hover over the grid for the latest result. Click it for a small menu with the
latest reply time, the average over the current buffer, the loss count, and a
Quit item. The checked **Show Screen Vignette** menu item controls the
full-screen warning. Its setting is remembered across launches.

## How it samples

The app runs `/sbin/ping -n -c 1 -t 2 8.8.8.8`, one ping at a time, with a
two second pause between the end of one ping and the start of the next. The
two second deadline passed to ping is what defines a lost sample: if no reply
arrives within it, the sample is recorded as lost and renders red. On a
healthy connection this means a new sample roughly every two seconds, so the
grid covers about the last twenty seconds; when the network is down each
sample takes the full deadline, so the grid covers about the last
half-minute.

The thresholds, sampling cadence, and target host are constants at the top of
[main.swift](main.swift).

## Building and running

Requires the Xcode command line tools (for `swiftc`). There is no Xcode
project; the whole app is one Swift file.

```sh
make            # builds ./pingbar
./pingbar       # runs the menu bar indicator in the foreground
./pingbar --once  # runs a single ping, prints the result, and exits
```

`./pingbar --once` exits with status 0 on a reply and 1 on a loss, so it can
also serve as a quick scriptable connectivity check.

## Installing as a login item

```sh
make install
```

This copies the binary to `~/.local/bin/pingbar`, generates a launchd user
agent at `~/Library/LaunchAgents/local.pingbar.plist` from the template in
this directory, and starts it. The agent starts the indicator at login and
restarts it if it crashes; quitting from the menu (a clean exit) does not
trigger a restart. Running `make install` again rebuilds, reinstalls, and
restarts the running indicator.

```sh
make uninstall
```

This stops the indicator and removes both the agent plist and the installed
binary.
