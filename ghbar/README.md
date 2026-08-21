# ghbar

A macOS menu bar indicator that shows whether the checks GitHub runs on one
branch of one repository are passing. The indicator watches the head commit of
the branch, so it follows the branch as new commits land on it.

## What the indicator means

The shape changes along with the color, so the indicator still reads when red
and green do not.

- **Green tick**: every check that reached a verdict passed.
- **Red cross in an octagon**: at least one check failed. A check that was
  cancelled, timed out, or asked for action counts as a failure, which is also
  how GitHub's own summary counts them.
- **Orange clock**: no check has failed, and at least one is still queued or
  running.
- **Gray dash**: GitHub reported no checks on the commit, or reported only
  checks that were skipped or neutral.
- **Gray question mark**: the state is not known, because the last request to
  GitHub did not work. The menu says why.
- **Gray gear**: no repository has been chosen yet.

Next to the indicator is the repository's name, so several indicators can be
told apart at a glance. The name can be replaced with one of your own, or
turned off, in the settings.

## The menu

Clicking the indicator opens a menu that names the repository and branch, sums
up the checks, and names the head commit. Checks that GitHub counted but would
not name are counted separately rather than passed over. Below that come the
checks that are failing or still running, each of which opens its own page on
GitHub when clicked. Everything else is in the **All Checks** submenu, which
lists every check on the commit, failures first. **Open Checks on GitHub**
opens the head commit's checks page.

The line above the last separator names the indicator's number, when it last
heard from GitHub, and how much of the hourly request allowance is left.

The menu keeps changing while it is open, so a check that finishes while you
are reading moves as it finishes.

## When the branch turns red

A banner drops out of the menu bar item. It is a piece of red velour with a
gold bullion fringe and a heavier tassel at each bottom corner, half the width
of the screen and a third of its height, with a gold braid set inside its
border and a lozenge at each corner of that. It slides out from behind the menu
bar still rolled, arriving a shade below where it settles, lifts again in a
wind-up big enough to read as one, and then pays out under gravity, gathering
speed the whole way and running out of itself at the bottom, where it
overshoots and rings itself still. It hangs long enough to be read, rolls back
up faster and faster as the roll thickens, bobs down out from under the bar as
it swallows the last of the cloth, and withdraws behind the menu bar it came
from at the speed the flourish hands over. It is over when the cloth has
actually gone rather than when the clock says it should have: the rod is the
only thing the withdrawal moves, and the cloth below follows on its own springs
and lags behind it. Worked into it in gold thread are the repository and
branch, how many checks failed, and the names of the ones that did. Nothing
about it can be clicked; the pointer goes straight through it.

Nothing is ever faded in or out. Both ends of the animation pass through the
same pose, with the roll entirely above the top of its own window and so
behind the menu bar, which means the banner arrives by coming out from behind
an edge and leaves by going back behind it. An object that emerges from an
occluding edge has come from somewhere; an object that simply appears has
not arrived at all.

It drops when a branch that was passing starts failing, and again when a
branch that is already failing breaks in some new way, so that a second thing
going wrong is not silent. It does not drop for the first answer GitHub gives
after an indicator starts: a branch that was already red is not a branch that
has just turned, and logging in should not fill the screen with banners.

The cloth is cloth rather than a picture of one. It is a grid of point masses
joined by distance constraints, advanced by Verlet integration, hanging from a
line of fixed points along the top edge, pulled about by gravity and a
draught that travels across and down it in gusts, and held back by the air
against the square of its speed. That last is what tells the great swing the
cloth arrives with from the small stir it should keep afterwards: a damping
merely proportional to speed cannot, and the banner swings a third of its own
width toward the camera and appears to pump in and out for the whole of the
hang. The cloth is cut wider than
the bar it hangs from, as real banners are, because cloth cut to the exact
width of its support hangs flat, and flat velvet is only red card: everything
that makes the fabric worth looking at happens along a fold. It resists being
bent, which is what decides how wide a fold comes out and what stops the
gather from crimping into narrow creases at the heading and hanging flat
below. Its hem is weighted like a real one, so the bottom edge hangs with
authority and scallops where the folds come through it rather than ruling a
straight line. The fringe is a hundred and fifty cords of its own, each a
little chain of knots hanging from the hem, and it is the part that moves
last and settles last, which is what tells the eye how heavy everything above
it is. The nine solved knots of a cord are drawn as a curve through
thirty-two points, because eight straight facets with seven and a half twists
spread over them read as a bicycle chain. Each cord is cut a little longer or
shorter than its neighbours, swings at its own rate, and is tied a little
either side of where the arithmetic would put it; neighbouring cords push
apart when they touch and pull weakly toward one another when they do not, so
the hem falls into bunches of two and three rather than standing as a comb. A
cord may not climb back up past the point it is tied to and may not sink into
the cloth it hangs from, which is what stops one standing up through the face
of the banner when the hem is snatched away, and the air holds them back
against the square of their speed, which is what stops the snap at the bottom
of the drop straightening every one of them into a spike. When the banner
winds up, the cords are gathered onto the roll root first and tip last, along
a spiral long enough for the heavy tassels at the corners, and they are left
sitting proud of the outermost wrap: bullion is a wire coil around a thread,
stiffer than the cloth it is sewn to, and it will not be drawn in to the
core.

The drop is a speed rather than a curve. The cloth is already travelling at
the roll's surface speed at the moment it is let go, gravity is added to that
and the air takes some of it away, and the phase ends when the cloth runs out
rather than when a clock says so. That speed is set to about the rate the
cloth below actually falls. Let off faster than that, the surplus has nowhere
to go and gathers in the lower half, the letters of the headline run into one
another, and the leading edge stalls while the slack builds and then lurches
as it is taken up. What happens at that moment is the point:
the hem keeps the speed it had, the cloth above it goes taut, and the banner
rings. Shaping a phase's own clock instead — squaring it, say — gives the
payout no rate at all to begin with, so the banner hangs trembling for a
fifth of a second and then lurches.

What has not been let out of the roll yet is not simulated but wound onto a
spiral beneath what has. The roll is built from a thickness rather greater than
the cloth's own, because cloth with a pile on it and a fringe sewn to one end
does not wind tightly, and because a roll as thin as the arithmetic allows is a
rod rather than a roll and does not visibly thicken as it takes the banner up.
The frame the roll is built in is anchored to gravity. A hanging cloth runs
downward; it may lean, but it may not point sideways or upward, so the
direction measured off the cloth is only allowed to bend the vertical rather
than to replace it, and which side of the cloth the roll sits on follows from
that. Deciding the side afresh from the cloth alone, and then taking the
direction the cloth runs back out of the same frame, leaves the two turning
each other round: over one drop the frame went through a complete revolution,
which swung the roll a quarter of a banner's width toward the camera, carried
the hem up above the rod it hangs from, and ran the artwork backwards across
the face of the roll.

No thread in a woven cloth gives more than a few percent before it simply
refuses, so after the relaxation passes there are passes that act as a
ceiling rather than as a spring. Without them the cloth quietly stretches
under the weight of the hem arriving, and the bottom of the banner comes out
a quarter wider than the top, which no hanging cloth does.

The solver runs on a coarse grid because that is what it can afford, and its
points land about eighteen pixels apart, which would make a polyline of the
hem. The drawn mesh is a smooth surface fitted through the solved points
instead, which costs nothing worth counting and takes the turn at each corner
of the hem down from ten degrees to two.

The shading is built the other way round from an ordinary surface. Velvet and
velour are pile fabrics, a dense forest of short fibres standing off the
backing, and light that arrives head on is swallowed between the fibres rather
than bounced back. That is why the flat of a velvet banner reads almost black
while every fold and every edge lights up. So the cloth is given a dark
diffuse base to carry the colour, and over it a sheen that rises towards
grazing angles, which is the Charlie distribution with Ashikhmin's visibility
term. The lettering is set in a slab-serifed
Egyptian with inscriptional capitals above it, because a face drawn for a
screen has hairlines a tenth the width of its stems, and a hairline cannot
exist in thread: the thinnest thing thread makes is one stitch. It stands
proud of the cloth by a height taken from its own coverage blurred by about
half a stroke, so the whole width of a stroke turns rather than a rim of it,
and it shades the cloth immediately around itself. A pile too fine to see as
texture tilts the surface a fraction of a degree at a time; it is there to
break up the sheen rather than to be seen, and there are faint bands across
the cloth where the pile has been crushed by being rolled, one band to each
turn of the roll, which is most of the difference between cloth that has been
somewhere and cloth woven this morning. Where the fringe hangs against the
cloth it takes some of the colour bouncing off it,
and at any strength worth noticing it stops being cloth and becomes a
hammered surface. Light that goes into the pile, turns over once and comes
back out has passed through the dye twice, so just off the crown of a fold is
the most saturated red anywhere on the banner. The lettering stands proud of
the cloth it is worked into and gathers light from the whole sky, as a metal
does, so it never sinks to the colour of the cloth beneath it. The folds cast
shadows on one another. The back of the cloth is not the front with the
printing left off: the pile stands on one side only, and the woven ground
that carries it is flatter, greyer and duller, with a faint ghost of the
artwork where the dye has struck through.

The banner throws a shadow onto whatever is behind the window, without which
it is a picture stuck on the screen rather than a thing hanging in front of
it. The shape of that shadow is drawn once, with the depth test on, into a
small single-channel image, then blurred across and down and laid behind the
banner in one pass. Drawing it once is what keeps it an even tone: drawn with
blending it darkens itself wherever two folds of the same cloth overlap, and
an opaque thing does not throw a darker shadow where it happens to be doubled
over. Depth testing it is what keeps cords wound away inside the roll from
stamping their shape on the wall.

A camera whose shutter is open for a sixtieth of a second records everything
the cloth did during that sixtieth, not where it ended up. The solver runs
four times in that sixtieth, so all four positions are known before the frame
is drawn, and a frame that is moving quickly is drawn from all four and
averaged. That is the integral itself rather than an imitation of it: carrying
part of the previous frame forward instead gives an endless tail of whole-frame
copies, which shows as the headline printed two and three times over and as
cloth you can see through. Frames that are barely moving, which is every frame
while the banner hangs and every frame in slow motion, are drawn once and are
perfectly sharp.

The eight-bit write is dithered by half a code value, because across the wide
soft gradient of a fold eight bits cannot hold the steps apart and they read
as contour lines drawn on the cloth.

The tone curve is run on each channel of the cloth and on the brightness
alone of the thread. Run per channel, a filmic curve pulls a saturated colour
toward white as it brightens, because whichever channel is already high is
compressed hardest. On red cloth that is what a photograph does and it is
wanted. On gold it is not, and gold that has been pulled two thirds of the
way to grey is brass.

Running `ghbar --banner` hangs one straight away with a made up failure on it,
and quits when it has rolled back up. There is a quieter way to do the same
thing without leaving the menu bar, for showing the banner to somebody rather
than waiting for a branch to oblige: hold Option and **New Indicator** becomes
**Drop the Banner**, which hangs whatever failure the branch is carrying and
invents one when the branch is fine. It changes as the key goes down and
changes back as it comes up, so it can be found and lost again with the menu
standing open, and Command-Option-N does the same thing.

Holding Shift while a banner is on the screen runs it at a tenth speed, as
holding Shift has always done to a window on its way into the Dock. The key
is read afresh every frame, so it can be pressed and released part way
through and the cloth carries on from where it had got to. It is the same
motion either way rather than a different, livelier cloth played slowly: the
solver takes steps of one fixed length whatever the frame rate, and
everything else is put on that same clock. The choreography, the cloth and
the fringe all move together, a step at a time. Running the choreography on
the display's clock instead leaves the rod moving during frames when the
cloth it holds up cannot, and the cloth then catches up all at once, which at
a tenth speed is two or three frames of lag followed by a jump, over and
over.

## Settings

**Settings…** opens a window with the repository, the branch, the name to draw
in the menu bar, and how often to ask GitHub. The repository can be written as
`owner/name`, or pasted in any of the forms GitHub hands out: the address of
the repository, the address of a branch, or the SSH remote. Pasting the address
of a branch fills in the branch as well.

Settings are remembered per indicator, in `~/Library/Preferences`, under
`local.ghbar.` followed by the indicator's number.

## Running several indicators

Each running indicator holds one instance number, from 1 upwards. The number
picks the settings it reads, the lock file it holds, and the launchd agent it
writes, so two indicators never tread on each other. **New Indicator** in the
menu starts another copy, which takes the lowest number no other indicator is
using; running the program again from a shell does the same thing.

An indicator started with `--instance N` takes that exact number. If another
indicator is already running as N, the new one says so and exits, which is what
keeps a login from starting a second copy of an indicator you already have.

## Starting at login, and stopping

Every launch writes `~/Library/LaunchAgents/local.ghbar.N.plist`, a launchd
agent that starts that indicator again at the next login. Several indicators
mean several agents, one per number, and every one of them starts at login.
The agent points at wherever the program was run from, so running the copy in
`~/.local/bin` is what registers that copy.

**Disable and Quit** deletes that indicator's agent, stops its launchd job, and
exits. The indicator stops coming back at login. Its settings stay behind, so
starting an indicator with that number again picks up where it left off.

**Quit ghbar** exits without touching the agent, so the indicator returns at
the next login.

## The GitHub token

Public repositories need no token. Private ones do, and a token also raises the
hourly request allowance from sixty to five thousand.

launchd starts login items with almost no environment, so an indicator started
at login cannot read the `GH_TOKEN` your terminal reads. Rather than keep a
copy of the token, the app asks your login shell for one: it runs your shell as
an interactive login shell, which reads the same profile files an interactive
terminal reads, and takes `GH_TOKEN`, then `GITHUB_TOKEN`, then whatever
`gh auth token` prints. The settings window names which of those the token came
from. The answer is remembered until GitHub rejects it, at which point the app
asks the shell again.

GitHub does not offer the **Checks** permission to fine-grained personal
access tokens at all; only a GitHub App can be granted it. Searching for it in
the token editor finds nothing, because there is nothing to find. Reading a
private repository's check runs through the REST API is therefore closed to
the kind of token GitHub now recommends.

What is not closed is GitHub's own verdict. Ask through the GraphQL API and it
answers with the state it has computed across every check on the commit,
whether or not the token may look at those checks one by one. That is the
answer this app wants, so whenever it has a token it asks that way, and a
private repository works with an ordinary fine-grained token.

What such a token loses is the list, not the color. The indicator is right,
the menu reports the verdict and how many checks it was taken over, and it
says how many of them it could not name: "Passing: 41 not listed by this
token". Read-only **Contents** is what gives the line naming the head commit.
A classic token with the `repo` scope can name every check.

Adding a permission to a token an organization has already approved can put
that token back in front of the organization's owners, so a permission that
has been added but not yet approved reads exactly like one that was never
added.

## Requests and the rate limit

With a token, each poll is one GraphQL query, which brings back the verdict,
the head commit, and as many of the checks as the token may see. It costs one
point of an hourly five thousand, so a poll a minute spends sixty.

Without a token GraphQL is closed, since it serves no anonymous callers, and
the REST endpoints are asked instead: the branch's combined commit status and
the check runs on the head commit each poll, and the commit message once per
commit. Both polled requests carry the ETag from the previous answer, and
GitHub charges nothing for an answer of "nothing has changed", so an idle
branch costs almost none of the sixty requests an hour an anonymous caller
gets.

## Building and running

Requires the Xcode command line tools, for `swiftc`. There is no Xcode
project: the indicator is one Swift file and the banner is another, and the
shaders are compiled at runtime, since the offline Metal compiler ships only
with Xcode.

```sh
make            # builds ./ghbar
./ghbar         # runs an indicator, taking the lowest free instance number
```

The first indicator with nothing configured opens its settings window.

```
usage: ghbar [--instance N] [--repo OWNER/NAME] [--branch BRANCH]
             [--once] [--banner] [--no-startup]

  --instance N   run as indicator N rather than the lowest free number
  --repo R       track this repository, and remember it for this indicator
  --branch B     track this branch, and remember it for this indicator
  --once         report the branch on standard output and exit, without
                 touching the menu bar; exits 0 when the branch is passing,
                 1 when it is failing, and 2 otherwise
  --banner       hang the red banner straight away and quit when it has
                 rolled back up, to see what it looks like
  --no-startup   leave the login item alone for this run
```

`--once` also works as a scriptable check on a branch:

```sh
ghbar --once --repo commontoolsinc/labs --branch main
```

## Installing

```sh
make install
"$HOME/.local/bin/ghbar" &
```

`make install` copies the binary to `~/.local/bin/ghbar`. Running that copy
once is what writes the launchd agent pointing at it, so the indicator comes
back at every login from then on.

```sh
make uninstall
```

This stops and removes every `local.ghbar.*` agent and deletes the installed
binary. Settings and lock files are left alone; they live in
`~/Library/Preferences` and `~/Library/Application Support/ghbar`.
