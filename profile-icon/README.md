# profile-icon

Removes the white background surrounding a badge and makes a 512-by-512
transparent PNG. The input stays unchanged. The output keeps the original
canvas proportions and margins; rectangular inputs get transparent padding.

```sh
profile-icon "$HOME/Downloads/My Badge.png"
profile-icon input.png -o output.png
```

Without `-o`, the output is `My Badge (512).png` beside the input. An existing
output is never overwritten. The command prints the output's absolute path.

## Processing

The command follows near-white pixels from every edge of an input,
using all eight neighboring pixels. A pixel is near white when all three
color channels are at least 224 out of 255. Only the connected background
becomes transparent, so white details enclosed by the badge stay intact.

At the boundary, foreground samples from the surrounding five-by-five pixels
estimate the coverage of the artwork. The background color is the median of
the opaque near-white edge pixels. The command chooses the lowest coverage
whose color mixture reproduces each original color channel within three levels
out of 255. It restores partial transparency and removes the background's
contribution from the edge colors. Other artwork retains its color and opacity.
The result is resized with the triangle filter, whose nonnegative weights
preserve the transparent margins and opaque interior.

An input with transparency at the canvas edge is resized with its existing
alpha channel. This preserves cutouts, including white artwork touching the
edge. Interior partial opacity is also preserved when removing an opaque
white exterior. Animation, opaque images without a white outer background,
and all-white opaque images are rejected.

## Install and test

Requires Python 3 and ImageMagick 7 (`brew install imagemagick`).

```sh
chmod +x profile_icon.py
ln -s "$PWD/profile_icon.py" ~/bin/profile-icon
python3 -m unittest -v
```
