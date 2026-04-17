#!/usr/bin/env python3
"""
Convert animated SVGs to Slack emoji GIFs.

Renders each SVG via Playwright (reuses svg2frames logic), crops each frame
to the union alpha bounding box across all frames (no padding/margins),
scales so the largest dimension is 128px, and writes an animated GIF with
transparent background.

Usage:
    python tools/svg2slack_emoji.py [input_dir] [output_dir]
      --fps 12       Frame rate (default: 12)
      --size 128     Max dimension in pixels (default: 128)
      --scale 8      SVG render scale before downscaling (default: 8)
"""

import argparse
import io
import shutil
import sys
import tempfile
from pathlib import Path

# Force UTF-8 on Windows consoles so svg2frames' arrow chars don't blow up
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Import the existing renderer
sys.path.insert(0, str(Path(__file__).parent))
from svg2frames import detect_animation_duration, ensure_playwright, render_frames  # noqa: E402

from PIL import Image  # noqa: E402


def find_union_bbox(frame_paths):
    """Union alpha bounding box across all frames."""
    bbox = None
    for p in frame_paths:
        img = Image.open(p).convert("RGBA")
        fb = img.getbbox()  # alpha-aware bbox of non-zero pixels
        if fb is None:
            continue
        if bbox is None:
            bbox = fb
        else:
            bbox = (
                min(bbox[0], fb[0]),
                min(bbox[1], fb[1]),
                max(bbox[2], fb[2]),
                max(bbox[3], fb[3]),
            )
    return bbox


def crop_and_scale_frames(frame_paths, bbox, max_size):
    """Crop each frame to bbox and scale so max dim == max_size."""
    cropped = []
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    if w >= h:
        new_w = max_size
        new_h = max(1, round(h * max_size / w))
    else:
        new_h = max_size
        new_w = max(1, round(w * max_size / h))

    for p in frame_paths:
        img = Image.open(p).convert("RGBA").crop(bbox)
        # NEAREST keeps pixel-art crisp
        img = img.resize((new_w, new_h), Image.NEAREST)
        cropped.append(img)
    return cropped, new_w, new_h


def to_gif_palette(frame):
    """
    Convert an RGBA frame to a palettized 'P' image where index 0 is reserved
    for transparency. Fully transparent pixels map to index 0.
    """
    rgba = frame
    # Pixels with alpha > 128 are opaque; others transparent.
    alpha = rgba.split()[-1]
    mask = alpha.point(lambda a: 255 if a > 128 else 0)

    # Quantize RGB (with a white background behind transparent pixels so they
    # don't contribute to palette). We'll mask them to index 0 after.
    rgb = Image.new("RGB", rgba.size, (0, 0, 0))
    rgb.paste(rgba, mask=mask)
    pal = rgb.quantize(colors=255, method=Image.MEDIANCUT, dither=Image.NONE)

    # Shift all palette indices by 1 so index 0 is free for transparency.
    idx_arr = pal.load()
    w, h = pal.size
    out = Image.new("P", pal.size, 0)
    out_px = out.load()
    mask_px = mask.load()
    for y in range(h):
        for x in range(w):
            if mask_px[x, y] == 0:
                out_px[x, y] = 0
            else:
                out_px[x, y] = idx_arr[x, y] + 1

    # Build new palette: index 0 = transparent placeholder, 1..256 = shifted.
    src_pal = pal.getpalette()  # 256*3
    new_pal = [0, 0, 0] + src_pal[: 255 * 3]
    # Pad to full 256 colors (PIL requires 768 entries).
    if len(new_pal) < 768:
        new_pal += [0] * (768 - len(new_pal))
    out.putpalette(new_pal)
    out.info["transparency"] = 0
    return out


def save_gif(frames, output_path, fps):
    """Write frames as animated GIF with transparent background."""
    duration_ms = int(round(1000.0 / fps))
    palette_frames = [to_gif_palette(f) for f in frames]
    palette_frames[0].save(
        output_path,
        save_all=True,
        append_images=palette_frames[1:],
        duration=duration_ms,
        loop=0,
        disposal=2,  # restore to background between frames (needed for transparency)
        transparency=0,
        optimize=False,
    )


SLACK_MAX_BYTES = 128 * 1024


def convert_one(svg_path: Path, output_path: Path, fps: float, max_size: int, scale: float, max_frames: int):
    tmp = Path(tempfile.mkdtemp(prefix="svg2slack_"))
    try:
        svg_text = svg_path.read_text(encoding="utf-8")
        duration = detect_animation_duration(svg_text)

        # Cap total frame count by lowering effective fps for very long animations.
        eff_fps = fps
        if duration * fps > max_frames:
            eff_fps = max(2.0, max_frames / duration)
            print(f"  (long animation {duration:.1f}s — reducing fps {fps} -> {eff_fps:.1f})")

        saved, cw, ch = render_frames(
            svg_path=svg_path,
            output_dir=tmp,
            fps=eff_fps,
            duration=duration,
            scale=scale,
            background="transparent",
        )
        bbox = find_union_bbox(saved)
        if bbox is None:
            print(f"  WARN: {svg_path.name} renders fully transparent, skipping")
            return
        frames, nw, nh = crop_and_scale_frames(saved, bbox, max_size)
        save_gif(frames, output_path, eff_fps)
        sz_kb = output_path.stat().st_size / 1024
        warn = "  (OVER 128KB limit)" if output_path.stat().st_size > SLACK_MAX_BYTES else ""
        print(f"  -> {output_path.name}: {nw}x{nh}, {len(frames)} frames, {sz_kb:.1f} KB{warn}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Convert animated SVGs to Slack emoji GIFs")
    parser.add_argument(
        "input_dir",
        nargs="?",
        default="assets/svg-animations",
        help="Directory of input SVGs (default: assets/svg-animations)",
    )
    parser.add_argument(
        "output_dir",
        nargs="?",
        default="assets/slack-emojis",
        help="Directory for output GIFs (default: assets/slack-emojis)",
    )
    parser.add_argument("--fps", type=float, default=12.0)
    parser.add_argument("--size", type=int, default=128)
    parser.add_argument("--scale", type=float, default=8.0)
    parser.add_argument("--max-frames", type=int, default=60,
                        help="Cap total frame count (auto-reduces fps for long animations, default: 60)")
    parser.add_argument("--only", help="Only process this SVG stem (e.g. clawd-happy)")
    args = parser.parse_args()

    ensure_playwright()

    in_dir = Path(args.input_dir)
    out_dir = Path(args.output_dir)
    if not in_dir.is_dir():
        print(f"Error: {in_dir} not a directory")
        sys.exit(1)
    out_dir.mkdir(parents=True, exist_ok=True)

    svgs = sorted(in_dir.glob("*.svg"))
    if args.only:
        svgs = [s for s in svgs if s.stem == args.only]
    if not svgs:
        print(f"No SVGs found in {in_dir}")
        sys.exit(1)

    print(f"Converting {len(svgs)} SVG(s) -> {out_dir} (max {args.size}px, {args.fps} fps)")
    for svg in svgs:
        print(f"\n[{svg.name}]")
        out_path = out_dir / f"{svg.stem}.gif"
        try:
            convert_one(svg, out_path, args.fps, args.size, args.scale, args.max_frames)
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR: {e}")


if __name__ == "__main__":
    main()
