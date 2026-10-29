import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

from profile_icon import magick, remove_background

SCRIPT = Path(__file__).with_name("profile_icon.py")
WHITE = (255, 255, 255, 255)
NAVY = (15, 35, 55, 255)


def image(width, height, color=WHITE):
    return bytearray(color * (width * height))


def pixel(pixels, width, x, y):
    return tuple(pixels[(y * width + x) * 4 : (y * width + x + 1) * 4])


def put(pixels, width, x, y, color):
    pixels[(y * width + x) * 4 : (y * width + x + 1) * 4] = bytes(color)


def badge(width, height):
    pixels = image(width, height)
    for y in range(2, height - 2):
        for x in range(2, width - 2):
            put(pixels, width, x, y, NAVY)
    return pixels


class BackgroundTests(unittest.TestCase):
    def test_removes_background_and_preserves_enclosed_pale_artwork(self):
        pixels = badge(15, 15)
        put(pixels, 15, 7, 7, WHITE)
        put(pixels, 15, 7, 8, (245, 240, 230, 255))
        original = pixels[:]
        result = remove_background(pixels, 15, 15)
        self.assertEqual(pixel(result, 15, 0, 0)[3], 0)
        self.assertEqual(pixel(result, 15, 14, 14)[3], 0)
        self.assertEqual(pixel(result, 15, 7, 7), WHITE)
        self.assertEqual(pixel(result, 15, 7, 8), (245, 240, 230, 255))
        self.assertEqual(pixel(result, 15, 4, 4), NAVY)
        self.assertEqual(pixels, original)

    def test_follows_diagonal_gaps_and_uses_all_edges(self):
        pixels = image(15, 15, NAVY)
        for x, y in [(0, 0), (1, 1), (2, 2), (7, 14), (7, 13), (14, 7)]:
            put(pixels, 15, x, y, (250, 249, 248, 255))
        put(pixels, 15, 7, 7, WHITE)
        result = remove_background(pixels, 15, 15)
        for x, y in [(0, 0), (1, 1), (2, 2), (7, 14), (7, 13), (14, 7)]:
            self.assertEqual(pixel(result, 15, x, y)[3], 0)
        self.assertEqual(pixel(result, 15, 7, 7), WHITE)

    def test_recovers_antialiased_coverage_without_white_fringe(self):
        pixels = image(11, 11)
        foreground = (20, 40, 60)
        for margin, alpha in [(1, 0.08), (2, 0.5), (3, 1)]:
            color = tuple(round(c * alpha + 255 * (1 - alpha)) for c in foreground) + (
                255,
            )
            for y in range(margin, 11 - margin):
                for x in range(margin, 11 - margin):
                    put(pixels, 11, x, y, color)
        result = remove_background(pixels, 11, 11)
        for x, expected_alpha in [(1, 20), (2, 128), (3, 255)]:
            actual = pixel(result, 11, x, 5)
            self.assertAlmostEqual(actual[3], expected_alpha, delta=1)
            for channel, expected in zip(actual[:3], foreground):
                self.assertAlmostEqual(channel, expected, delta=5)
        self.assertEqual(pixel(result, 11, 0, 0)[3], 0)

    def test_preserves_existing_transparency(self):
        pixels = image(3, 3, (12, 45, 78, 0))
        put(pixels, 3, 1, 1, (245, 240, 230, 128))
        self.assertEqual(remove_background(pixels, 3, 3), pixels)

    def test_preserves_white_artwork_touching_an_existing_cutout_edge(self):
        pixels = image(5, 5, (0, 0, 0, 0))
        for y in range(3):
            put(pixels, 5, 2, y, WHITE)
        self.assertEqual(remove_background(pixels, 5, 5), pixels)

    def test_preserves_partial_opacity_at_an_existing_cutout_edge(self):
        pixels = badge(15, 15)
        put(pixels, 15, 0, 0, (245, 240, 230, 128))
        self.assertEqual(remove_background(pixels, 15, 15), pixels)

    def test_removes_opaque_background_without_changing_translucent_artwork(self):
        pixels = badge(15, 15)
        put(pixels, 15, 7, 7, (245, 240, 230, 128))
        result = remove_background(pixels, 15, 15)
        self.assertEqual(pixel(result, 15, 0, 0)[3], 0)
        self.assertEqual(pixel(result, 15, 7, 7), (245, 240, 230, 128))

    def test_preserves_multicolored_opaque_edges(self):
        pixels = badge(15, 15)
        put(pixels, 15, 2, 7, (0, 100, 255, 255))
        result = remove_background(pixels, 15, 15)
        self.assertEqual(pixel(result, 15, 0, 0)[3], 0)
        self.assertEqual(pixel(result, 15, 2, 7), (0, 100, 255, 255))

    def test_handles_enclosed_artwork_matching_a_near_white_background(self):
        pixels = image(5, 5, (224, 224, 224, 255))
        for y in range(1, 4):
            for x in range(1, 4):
                put(pixels, 5, x, y, (223, 255, 255, 255))
        put(pixels, 5, 2, 2, (224, 224, 224, 255))
        result = remove_background(pixels, 5, 5)
        self.assertEqual(pixel(result, 5, 0, 0)[3], 0)
        self.assertEqual(pixel(result, 5, 2, 2), (224, 224, 224, 255))

    def test_unmixes_edges_using_a_neighbor_with_the_matching_hue(self):
        pixels = badge(15, 15)
        put(pixels, 15, 2, 7, (128, 178, 255, 255))
        put(pixels, 15, 3, 7, (0, 100, 255, 255))
        result = remove_background(pixels, 15, 15)
        actual = pixel(result, 15, 2, 7)
        for channel, expected in zip(actual, (0, 100, 255, 128)):
            self.assertAlmostEqual(channel, expected, delta=1)
        self.assertEqual(pixel(result, 15, 3, 7), (0, 100, 255, 255))

    def test_rejects_all_background_and_missing_background(self):
        for pixels, message in [
            (image(4, 4), "only background"),
            (image(4, 4, NAVY), "no white outer"),
        ]:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(ValueError, message),
            ):
                remove_background(pixels, 4, 4)


class CommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.source = self.directory / "Badge [0] $(anything).png"
        self.write_image(self.source, 16, 16, badge(16, 16))

    def write_image(self, path, width, height, pixels):
        magick(
            ["-size", f"{width}x{height}", "-depth", "8", "rgba:-", f"png:{path}"],
            pixels,
        )

    def run_script(self, *arguments, env=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, arguments)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def test_creates_named_512_pixel_png_without_changing_source(self):
        original = self.source.read_bytes()
        result = self.run_script(self.source)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.source.with_name(self.source.stem + " (512).png")
        self.assertEqual(result.stdout.strip(), str(output))
        encoded = output.read_bytes()
        self.assertEqual(
            magick(["identify", "-format", "%m %wx%h", "png:-"], encoded),
            b"PNG 512x512",
        )
        pixels = magick(["png:-", "-depth", "8", "rgba:-"], encoded)
        self.assertEqual(pixel(pixels, 512, 0, 0)[3], 0)
        self.assertEqual(pixel(pixels, 512, 256, 256), NAVY)
        self.assertEqual(self.source.read_bytes(), original)

    def test_rectangular_input_keeps_its_aspect_ratio(self):
        self.write_image(self.source, 16, 8, badge(16, 8))
        output = self.directory / "rectangle.png"
        result = self.run_script(self.source, "-o", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        pixels = magick(["png:-", "-depth", "8", "rgba:-"], output.read_bytes())
        self.assertEqual(pixel(pixels, 512, 256, 80)[3], 0)
        self.assertEqual(pixel(pixels, 512, 256, 256), NAVY)
        self.assertEqual(pixel(pixels, 512, 256, 432)[3], 0)

    def test_rejects_existing_file_and_symlink_without_changing_them(self):
        output = self.directory / "existing.png"
        output.write_bytes(b"existing output")
        for destination in [output, self.directory / "link.png", self.source]:
            if destination.name == "link.png":
                destination.symlink_to(output)
            original = destination.read_bytes()
            with self.subTest(destination=destination):
                result = self.run_script(self.source, "-o", destination)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("File exists", result.stderr)
                self.assertEqual(destination.read_bytes(), original)
        self.assertTrue((self.directory / "link.png").is_symlink())
        self.assertEqual(list(self.directory.glob(".profile-icon-*")), [])

    def test_reports_missing_or_invalid_input_without_an_output(self):
        invalid = self.directory / "invalid.png"
        invalid.write_bytes(b"this is not a PNG")
        for source in [invalid, self.directory / "missing.png"]:
            with self.subTest(source=source):
                result = self.run_script(source)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("profile-icon:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(source.with_name(source.stem + " (512).png").exists())

    def test_rejects_animated_png(self):
        encoded = self.source.read_bytes()
        payload = b"acTL" + struct.pack(">II", 2, 0)
        chunk = struct.pack(">I", 8) + payload + struct.pack(">I", zlib.crc32(payload))
        self.source.write_bytes(encoded[:33] + chunk + encoded[33:])
        result = self.run_script(self.source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("one still PNG", result.stderr)

    def test_reports_missing_imagemagick(self):
        result = self.run_script(self.source, env={**os.environ, "PATH": ""})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("brew install imagemagick", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
