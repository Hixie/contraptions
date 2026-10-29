#!/usr/bin/env python3
"""Remove an icon's white outer background and make a 512-pixel PNG."""

import argparse
import os
import subprocess
import tempfile
from collections import deque
from pathlib import Path
from statistics import median


def neighbors(index, width, height, radius=1):
    x, y = index % width, index // width
    for ny in range(max(0, y - radius), min(height, y + radius + 1)):
        for nx in range(max(0, x - radius), min(width, x + radius + 1)):
            yield ny * width + nx


def border_pixels(width, height):
    for x in range(width):
        yield x
        yield (height - 1) * width + x
    for y in range(height):
        yield y * width
        yield y * width + width - 1


def remove_background(pixels, width, height):
    """Remove edge-connected near-white pixels and unmix their boundary."""
    if any(pixels[index * 4 + 3] < 255 for index in border_pixels(width, height)):
        return pixels
    outside = bytearray(width * height)
    pending = deque()
    boundary = set()

    def visit(index):
        if outside[index]:
            return
        if pixels[index * 4 + 3] == 0 or min(pixels[index * 4 : index * 4 + 3]) >= 224:
            outside[index] = 1
            pending.append(index)
        else:
            boundary.add(index)

    for index in border_pixels(width, height):
        visit(index)
    samples = [index for index in pending if pixels[index * 4 + 3] == 255]
    if not samples:
        if min(pixels[3::4]) < 255:
            return pixels
        raise ValueError("the opaque image has no white outer background")
    background = [
        median(pixels[index * 4 + channel] for index in samples) for channel in range(3)
    ]
    boundary.clear()
    while pending:
        for index in neighbors(pending.popleft(), width, height):
            visit(index)

    if all(outside):
        raise ValueError("the image contains only background")

    result = bytearray(pixels)
    for index, is_background in enumerate(outside):
        if is_background:
            result[index * 4 + 3] = 0

    band = boundary | {
        neighbor
        for index in boundary
        for neighbor in neighbors(index, width, height)
        if outside[neighbor]
    }
    for index in band:
        if pixels[index * 4 + 3] < 255:
            continue
        color = pixels[index * 4 : index * 4 + 3]
        alpha = 1
        for foreground in neighbors(index, width, height, 2):
            if outside[foreground]:
                continue
            sample = pixels[foreground * 4 : foreground * 4 + 3]
            difference = [bg - channel for bg, channel in zip(background, sample)]
            contrast = sum(delta * delta for delta in difference)
            if not contrast:
                continue
            coverage = max(
                0,
                min(
                    1,
                    sum(
                        (bg - channel) * delta
                        for bg, channel, delta in zip(background, color, difference)
                    )
                    / contrast,
                ),
            )
            if coverage < alpha and all(
                abs(channel - (bg * (1 - coverage) + fg * coverage)) <= 3
                for channel, bg, fg in zip(color, background, sample)
            ):
                alpha = coverage
        result[index * 4 + 3] = round(alpha * 255)
        if alpha:
            for channel in range(3):
                result[index * 4 + channel] = max(
                    0,
                    min(
                        255,
                        round(
                            (color[channel] - background[channel] * (1 - alpha)) / alpha
                        ),
                    ),
                )
    return result


def magick(arguments, data):
    return subprocess.run(
        ["magick", *arguments],
        input=data,
        capture_output=True,
        check=True,
    ).stdout


def process(source, output):
    encoded = source.read_bytes()
    position = 8
    while position + 12 <= len(encoded):
        length = int.from_bytes(encoded[position : position + 4], "big")
        if encoded[position + 4 : position + 8] == b"acTL":
            raise ValueError("expected one still PNG image")
        position += length + 12
    metadata = magick(["identify", "-format", "%w %h %n", "png:-"], encoded).split()
    if len(metadata) != 3 or metadata[2] != b"1":
        raise ValueError("expected one still PNG image")
    width, height = map(int, metadata[:2])
    pixels = magick(["png:-", "-colorspace", "sRGB", "-depth", "8", "rgba:-"], encoded)
    pixels = remove_background(pixels, width, height)
    with tempfile.TemporaryDirectory(
        prefix=".profile-icon-",
        dir=output.parent,
    ) as directory:
        candidate = Path(directory) / "icon.png"
        magick(
            [
                "-size",
                f"{width}x{height}",
                "-depth",
                "8",
                "rgba:-",
                "-filter",
                "Triangle",
                "-resize",
                "512x512",
                "-background",
                "none",
                "-gravity",
                "center",
                "-extent",
                "512x512",
                "-depth",
                "8",
                f"png:{candidate}",
            ],
            pixels,
        )
        os.link(candidate, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("png", type=Path, help="input PNG; kept unchanged")
    parser.add_argument("-o", "--output", type=Path, help="new output PNG path")
    args = parser.parse_args()
    source = args.png.absolute()
    output = (args.output or source.with_name(f"{source.stem} (512).png")).absolute()
    try:
        process(source, output)
    except FileNotFoundError as error:
        message = (
            "ImageMagick is required; install it with: brew install imagemagick"
            if error.filename == "magick"
            else str(error)
        )
        parser.exit(1, f"profile-icon: {message}\n")
    except subprocess.CalledProcessError as error:
        parser.exit(
            1, f"profile-icon: {error.stderr.decode(errors='replace').strip()}\n"
        )
    except (OSError, ValueError) as error:
        parser.exit(1, f"profile-icon: {error}\n")
    print(output)


if __name__ == "__main__":
    main()
