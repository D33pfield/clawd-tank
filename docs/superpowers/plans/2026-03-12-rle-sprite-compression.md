# RLE Sprite Compression Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compress sprite data from 13.3 MB to ~319 KB using RLE encoding, switch to single-frame decompression at runtime, and update flash config from 2 MB to 8 MB to match the actual ESP32-C6FH8 hardware.

**Architecture:** Sprite headers change from raw RGB565 arrays to packed RLE `(value, count)` pairs with a frame offset table. Scene rendering decompresses one frame at a time into a single ARGB8888 buffer (~130 KB) instead of pre-allocating all frames (up to 11.9 MB). The png2rgb565.py tool gains RLE output support.

**Tech Stack:** C11 (ESP-IDF 5.3.2 / native), Python 3 (Pillow), LVGL 9.5.0

---

## File Structure

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `firmware/main/rle_sprite.h` | RLE data structures + inline decoder |
| Modify | `firmware/main/scene.c` | Single-frame decompression, new `anim_def_t` |
| Modify | `tools/png2rgb565.py` | RLE encoding output |
| Modify | `firmware/sdkconfig.defaults` | Flash size 8 MB |
| Modify | `firmware/partitions.csv` | Expand factory partition |
| Create | `firmware/test/test_rle_decode.c` | Unit tests for RLE decoder |
| Modify | `firmware/test/Makefile` | Add RLE test target |
| Regenerate | `firmware/main/assets/sprite_*.h` | All 5 sprite headers (RLE format) |

---

## Chunk 1: Flash Config + RLE Decoder

### Task 1: Update Flash Size Configuration

**Files:**
- Modify: `firmware/sdkconfig.defaults`
- Modify: `firmware/partitions.csv`

- [ ] **Step 1: Add flash size to sdkconfig.defaults**

Add at the end of the "Partition table" section in `firmware/sdkconfig.defaults`:

```
# Flash (ESP32-C6FH8 has 8MB embedded flash)
CONFIG_ESPTOOLPY_FLASHSIZE="8MB"
```

- [ ] **Step 2: Expand factory partition in partitions.csv**

Replace the contents of `firmware/partitions.csv` with:

```csv
# Name,   Type, SubType, Offset,  Size, Flags
nvs,      data, nvs,     0x9000,  0x6000,
phy_init, data, phy,     0xf000,  0x1000,
factory,  app,  factory, 0x10000, 0x7F0000,
```

This gives the factory app ~7.9 MB (up from 1.9 MB).

- [ ] **Step 3: Delete stale sdkconfig to force regeneration from defaults**

Run: `rm firmware/sdkconfig`

The next `idf.py build` will regenerate `sdkconfig` from `sdkconfig.defaults` with the new flash size.

- [ ] **Step 4: Commit**

```bash
git add firmware/sdkconfig.defaults firmware/partitions.csv
git commit -m "feat: configure 8MB flash to match ESP32-C6FH8 hardware"
```

---

### Task 2: RLE Decoder — Write Failing Test

**Files:**
- Create: `firmware/test/test_rle_decode.c`
- Modify: `firmware/test/Makefile`

- [ ] **Step 1: Write RLE decoder test**

Create `firmware/test/test_rle_decode.c`:

```c
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>

/* We test the header directly */
#include "rle_sprite.h"

/* --- Test 1: basic decode ---------------------------------- */
static void test_basic_decode(void)
{
    /* RLE: 3x 0xAAAA, 2x 0xBBBB = 5 pixels */
    static const uint16_t rle[] = { 0xAAAA, 3, 0xBBBB, 2 };
    uint16_t out[5];
    rle_decode_rgb565(rle, out, 5);

    assert(out[0] == 0xAAAA);
    assert(out[1] == 0xAAAA);
    assert(out[2] == 0xAAAA);
    assert(out[3] == 0xBBBB);
    assert(out[4] == 0xBBBB);
    printf("  PASS: test_basic_decode\n");
}

/* --- Test 2: single-pixel runs ----------------------------- */
static void test_single_pixel_runs(void)
{
    static const uint16_t rle[] = { 0x1111, 1, 0x2222, 1, 0x3333, 1 };
    uint16_t out[3];
    rle_decode_rgb565(rle, out, 3);

    assert(out[0] == 0x1111);
    assert(out[1] == 0x2222);
    assert(out[2] == 0x3333);
    printf("  PASS: test_single_pixel_runs\n");
}

/* --- Test 3: decode to ARGB8888 with transparency ---------- */
static void test_decode_to_argb8888(void)
{
    /* 0x18C5 is the transparent key */
    static const uint16_t rle[] = { 0x18C5, 2, 0xF800, 1 };
    uint8_t out[3 * 4]; /* 3 pixels, 4 bytes each */
    rle_decode_argb8888(rle, out, 3, 0x18C5);

    /* Pixel 0: transparent — alpha must be 0 */
    assert(out[0 * 4 + 3] == 0x00);
    /* Pixel 1: transparent — alpha must be 0 */
    assert(out[1 * 4 + 3] == 0x00);
    /* Pixel 2: 0xF800 = pure red in RGB565 → R=0xFF, G=0, B=0, A=0xFF */
    assert(out[2 * 4 + 3] == 0xFF); /* alpha */
    assert(out[2 * 4 + 2] == 0xFF); /* red (channel index 2 = R in BGRA) */
    assert(out[2 * 4 + 1] == 0x00); /* green */
    assert(out[2 * 4 + 0] == 0x00); /* blue */
    printf("  PASS: test_decode_to_argb8888\n");
}

/* --- Test 4: frame offset indexing ------------------------- */
static void test_frame_offset_indexing(void)
{
    /* Two frames packed: frame0 = 2 pixels, frame1 = 3 pixels */
    static const uint16_t rle[] = {
        /* frame 0: 2px of 0x1234 */
        0x1234, 2,
        /* frame 1: 1x 0xAAAA, 2x 0xBBBB */
        0xAAAA, 1, 0xBBBB, 2,
    };
    /* Offsets in uint16_t words: frame0 starts at 0, frame1 at 2 (pairs), sentinel at end */
    static const uint32_t offsets[] = { 0, 2, 6 }; /* word offsets: 0, 2*2=4 words... */

    /* Actually: frame 0 data is rle[0..3] (2 pairs = 4 uint16_t words)
     * frame 1 data is rle[4..9] (3 pairs = 6 uint16_t words? no, 2 pairs = 4 words)
     * Let me fix: offsets are in uint16_t words into rle_data.
     * frame 0: rle[0],rle[1] (pair: 0x1234, 2)  → 2 words
     * But a pair is 2 words (value + count). frame 0 has 1 pair = 2 words.
     * frame 1: rle[2],rle[3],rle[4],rle[5] → 2 pairs = 4 words.
     * offsets = {0, 2, 6}
     */

    uint16_t frame0[2], frame1[3];
    rle_decode_rgb565(&rle[offsets[0]], frame0, 2);
    rle_decode_rgb565(&rle[offsets[1]], frame1, 3);

    assert(frame0[0] == 0x1234);
    assert(frame0[1] == 0x1234);
    assert(frame1[0] == 0xAAAA);
    assert(frame1[1] == 0xBBBB);
    assert(frame1[2] == 0xBBBB);
    printf("  PASS: test_frame_offset_indexing\n");
}

/* --- Test 5: large run (run_count > 256) ------------------- */
static void test_large_run(void)
{
    static const uint16_t rle[] = { 0x5555, 1000 };
    uint16_t out[1000];
    rle_decode_rgb565(rle, out, 1000);

    for (int i = 0; i < 1000; i++) {
        assert(out[i] == 0x5555);
    }
    printf("  PASS: test_large_run\n");
}

int main(void)
{
    printf("test_rle_decode:\n");
    test_basic_decode();
    test_single_pixel_runs();
    test_decode_to_argb8888();
    test_frame_offset_indexing();
    test_large_run();
    printf("All RLE tests passed.\n");
    return 0;
}
```

- [ ] **Step 2: Add RLE test to Makefile**

Add to `firmware/test/Makefile`, after the `test_config_store` rule:

```makefile
test_rle_decode: test_rle_decode.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)
```

And update the `test` target to include `test_rle_decode`:

```makefile
test: test_notification test_config_store test_rle_decode
	./test_notification
	./test_config_store
	./test_rle_decode
```

And update the `clean` target:

```makefile
clean:
	rm -f test_notification test_config_store test_rle_decode
```

- [ ] **Step 3: Run test — verify it fails (rle_sprite.h doesn't exist yet)**

Run: `cd firmware/test && make test_rle_decode`

Expected: compilation error — `rle_sprite.h: No such file or directory`

- [ ] **Step 4: Commit test**

```bash
git add firmware/test/test_rle_decode.c firmware/test/Makefile
git commit -m "test: add RLE decoder unit tests (red)"
```

---

### Task 3: RLE Decoder — Implementation

**Files:**
- Create: `firmware/main/rle_sprite.h`

- [ ] **Step 1: Write rle_sprite.h**

Create `firmware/main/rle_sprite.h`:

```c
#pragma once

#include <stdint.h>

/**
 * RLE sprite data format.
 *
 * Each frame is a sequence of (value, run_count) uint16_t pairs packed
 * contiguously into rle_data[]. frame_offsets[i] gives the uint16_t word
 * offset into rle_data where frame i begins; frame_offsets[frame_count]
 * is a sentinel pointing past the last frame's data.
 */
typedef struct {
    const uint16_t *rle_data;
    const uint32_t *frame_offsets; /* frame_count + 1 entries */
    int frame_count;
    int width;
    int height;
} rle_sprite_t;

/**
 * Decode RLE data to raw RGB565 pixels.
 *
 * @param rle       Pointer to (value, count) pairs
 * @param out       Output buffer (must hold pixel_count uint16_t values)
 * @param pixel_count  Number of pixels to decode (width * height)
 */
static inline void rle_decode_rgb565(const uint16_t *rle, uint16_t *out, int pixel_count)
{
    int pos = 0;
    while (pos < pixel_count) {
        uint16_t value = *rle++;
        uint16_t count = *rle++;
        for (uint16_t i = 0; i < count && pos < pixel_count; i++) {
            out[pos++] = value;
        }
    }
}

/**
 * Decode RLE data directly to ARGB8888 (LVGL format: B, G, R, A bytes).
 *
 * Pixels matching transparent_key get alpha = 0; all others get alpha = 0xFF.
 * RGB565 channels are expanded to 8-bit.
 *
 * @param rle              Pointer to (value, count) pairs
 * @param out              Output buffer (must hold pixel_count * 4 bytes)
 * @param pixel_count      Number of pixels (width * height)
 * @param transparent_key  RGB565 value treated as fully transparent
 */
static inline void rle_decode_argb8888(const uint16_t *rle, uint8_t *out,
                                       int pixel_count, uint16_t transparent_key)
{
    int pos = 0;
    while (pos < pixel_count) {
        uint16_t value = *rle++;
        uint16_t count = *rle++;

        /* Pre-compute the BGRA bytes for this run */
        uint8_t r5 = (value >> 11) & 0x1F;
        uint8_t g6 = (value >> 5)  & 0x3F;
        uint8_t b5 =  value        & 0x1F;
        uint8_t b = (uint8_t)((b5 << 3) | (b5 >> 2));
        uint8_t g = (uint8_t)((g6 << 2) | (g6 >> 4));
        uint8_t r = (uint8_t)((r5 << 3) | (r5 >> 2));
        uint8_t a = (value == transparent_key) ? 0x00 : 0xFF;

        for (uint16_t i = 0; i < count && pos < pixel_count; i++, pos++) {
            out[pos * 4 + 0] = b;
            out[pos * 4 + 1] = g;
            out[pos * 4 + 2] = r;
            out[pos * 4 + 3] = a;
        }
    }
}
```

- [ ] **Step 2: Run test — verify it passes**

Run: `cd firmware/test && make test_rle_decode && ./test_rle_decode`

Expected:
```
test_rle_decode:
  PASS: test_basic_decode
  PASS: test_single_pixel_runs
  PASS: test_decode_to_argb8888
  PASS: test_frame_offset_indexing
  PASS: test_large_run
All RLE tests passed.
```

- [ ] **Step 3: Commit**

```bash
git add firmware/main/rle_sprite.h
git commit -m "feat: add RLE sprite decoder (header-only)"
```

---

## Chunk 2: Python Encoder + Scene Refactor

### Task 4: Update png2rgb565.py for RLE Output

**Files:**
- Modify: `tools/png2rgb565.py`

- [ ] **Step 1: Add RLE encoding function to png2rgb565.py**

Add after the `format_pixel_array()` function:

```python
def rle_encode(pixels):
    """RLE-encode a list of pixel values into (value, count) pairs."""
    if not pixels:
        return []
    runs = []
    current_val = pixels[0]
    current_count = 1
    for pixel in pixels[1:]:
        if pixel == current_val and current_count < 65535:
            current_count += 1
        else:
            runs.append((current_val, current_count))
            current_val = pixel
            current_count = 1
    runs.append((current_val, current_count))
    return runs


def format_rle_array(runs, indent="    "):
    """Format RLE (value, count) pairs as a C array."""
    lines = []
    for i in range(0, len(runs), 8):
        chunk = runs[i:i + 8]
        pairs = ", ".join(f"0x{v:04X},{c}" for v, c in chunk)
        lines.append(f"{indent}{pairs},")
    return "\n".join(lines)
```

- [ ] **Step 2: Replace `generate_header()` with RLE version**

Replace the entire `generate_header()` function with:

```python
def generate_header(frames_data, prefix, width, height):
    """Generate a C header with RLE-compressed sprite data."""
    guard = f"{prefix.upper()}_FRAMES_H"
    frame_count = len(frames_data)

    # RLE-encode all frames and compute offsets
    all_rle_runs = []
    frame_offsets = []  # word offsets into packed data
    word_offset = 0
    for pixels in frames_data:
        frame_offsets.append(word_offset)
        runs = rle_encode(pixels)
        all_rle_runs.extend(runs)
        word_offset += len(runs) * 2  # 2 uint16_t words per run pair
    frame_offsets.append(word_offset)  # sentinel

    raw_size = frame_count * width * height * 2
    rle_size = word_offset * 2
    ratio = raw_size / rle_size if rle_size > 0 else 0

    lines = []
    lines.append(f"#ifndef {guard}")
    lines.append(f"#define {guard}")
    lines.append("")
    lines.append("/**")
    lines.append(f" * Auto-generated by png2rgb565.py (RLE compressed)")
    lines.append(f" * {frame_count} frame(s), {width}x{height} pixels")
    lines.append(f" * Raw: {raw_size:,} bytes, RLE: {rle_size:,} bytes ({ratio:.0f}x compression)")
    lines.append(f" * Transparent key: 0x{TRANSPARENT_KEY:04X}")
    lines.append(" */")
    lines.append("")
    lines.append("#include <stdint.h>")
    lines.append("")
    lines.append(f"#define {prefix.upper()}_WIDTH  {width}")
    lines.append(f"#define {prefix.upper()}_HEIGHT {height}")
    lines.append(f"#define {prefix.upper()}_FRAME_COUNT {frame_count}")
    lines.append(f"#define {prefix.upper()}_TRANSPARENT_KEY 0x{TRANSPARENT_KEY:04X}")
    lines.append("")

    # Frame offset table (N+1 entries)
    lines.append(f"static const uint32_t {prefix}_frame_offsets[{frame_count + 1}] = {{")
    for i in range(0, len(frame_offsets), 8):
        chunk = frame_offsets[i:i + 8]
        vals = ", ".join(str(o) for o in chunk)
        lines.append(f"    {vals},")
    lines.append("};")
    lines.append("")

    # Packed RLE data
    lines.append(f"static const uint16_t {prefix}_rle_data[] = {{")
    lines.append(format_rle_array(all_rle_runs))
    lines.append("};")
    lines.append("")
    lines.append(f"#endif // {guard}")
    lines.append("")

    return "\n".join(lines)
```

- [ ] **Step 3: Test the encoder with a simple roundtrip**

Run a quick manual validation from the repo root:

```bash
python3 -c "
import sys; sys.path.insert(0, 'tools')
from png2rgb565 import rle_encode

# Test roundtrip
pixels = [0x18C5]*100 + [0xF800]*5 + [0x18C5]*50
runs = rle_encode(pixels)
# Decode
decoded = []
for val, count in runs:
    decoded.extend([val] * count)
assert decoded == pixels, 'Roundtrip failed'
print('RLE roundtrip OK:', len(pixels), 'pixels ->', len(runs), 'runs')
"
```

Expected: `RLE roundtrip OK: 155 pixels -> 3 runs`

- [ ] **Step 4: Commit**

```bash
git add tools/png2rgb565.py
git commit -m "feat: switch png2rgb565.py to RLE-compressed output"
```

---

### Task 5: Refactor scene.c for Single-Frame RLE Decompression

**Files:**
- Modify: `firmware/main/scene.c`

This is the core change. Replace pre-allocation of all frames with on-demand single-frame decompression.

- [ ] **Step 1: Update includes and constants**

In `firmware/main/scene.c`, add the rle_sprite.h include after the existing includes:

```c
#include "rle_sprite.h"
```

- [ ] **Step 2: Replace `anim_def_t` struct**

Replace the existing `anim_def_t` typedef and `anim_defs[]` array (lines 40-96) with:

```c
typedef struct {
    const uint16_t *rle_data;
    const uint32_t *frame_offsets;
    int frame_count;
    int frame_ms;
    bool looping;
    int width;
    int height;
    int y_offset;
} anim_def_t;

static const anim_def_t anim_defs[] = {
    [CLAWD_ANIM_IDLE] = {
        .rle_data = idle_rle_data,
        .frame_offsets = idle_frame_offsets,
        .frame_count = IDLE_FRAME_COUNT,
        .frame_ms = IDLE_FRAME_MS,
        .looping = true,
        .width = IDLE_WIDTH,
        .height = IDLE_HEIGHT,
        .y_offset = 8,
    },
    [CLAWD_ANIM_ALERT] = {
        .rle_data = alert_rle_data,
        .frame_offsets = alert_frame_offsets,
        .frame_count = ALERT_FRAME_COUNT,
        .frame_ms = ALERT_FRAME_MS,
        .looping = false,
        .width = ALERT_WIDTH,
        .height = ALERT_HEIGHT,
        .y_offset = 8,
    },
    [CLAWD_ANIM_HAPPY] = {
        .rle_data = happy_rle_data,
        .frame_offsets = happy_frame_offsets,
        .frame_count = HAPPY_FRAME_COUNT,
        .frame_ms = HAPPY_FRAME_MS,
        .looping = false,
        .width = HAPPY_WIDTH,
        .height = HAPPY_HEIGHT,
        .y_offset = 28,
    },
    [CLAWD_ANIM_SLEEPING] = {
        .rle_data = sleeping_rle_data,
        .frame_offsets = sleeping_frame_offsets,
        .frame_count = SLEEPING_FRAME_COUNT,
        .frame_ms = SLEEPING_FRAME_MS,
        .looping = true,
        .width = SLEEPING_WIDTH,
        .height = SLEEPING_HEIGHT,
        .y_offset = 8,
    },
    [CLAWD_ANIM_DISCONNECTED] = {
        .rle_data = disconnected_rle_data,
        .frame_offsets = disconnected_frame_offsets,
        .frame_count = DISCONNECTED_FRAME_COUNT,
        .frame_ms = DISCONN_FRAME_MS,
        .looping = true,
        .width = DISCONNECTED_WIDTH,
        .height = DISCONNECTED_HEIGHT,
        .y_offset = 8,
    },
};
```

- [ ] **Step 3: Replace scene struct frame storage with single buffer**

Replace the `frame_dscs[96]` / `frame_bufs[96]` fields in `struct scene_t` with:

```c
    /* Clawd sprite */
    lv_obj_t *sprite_img;
    lv_image_dsc_t frame_dsc;     /* single frame descriptor */
    uint8_t *frame_buf;           /* single ARGB8888 buffer */
    int frame_buf_size;           /* current buffer size in bytes */
    clawd_anim_id_t cur_anim;
    int frame_idx;
    uint32_t last_frame_tick;
```

- [ ] **Step 4: Replace helper functions**

Remove `free_frame_bufs()` and `build_frame_dscs()`. Replace with:

```c
static void ensure_frame_buf(scene_t *s, int w, int h)
{
    int needed = w * h * 4; /* ARGB8888 */
    if (s->frame_buf && s->frame_buf_size >= needed) return;
    free(s->frame_buf);
    s->frame_buf = malloc(needed);
    s->frame_buf_size = s->frame_buf ? needed : 0;
}

static void decode_and_apply_frame(scene_t *s)
{
    const anim_def_t *def = &anim_defs[s->cur_anim];
    int idx = s->frame_idx;
    if (idx >= def->frame_count) idx = def->frame_count - 1;

    int w = def->width;
    int h = def->height;
    ensure_frame_buf(s, w, h);
    if (!s->frame_buf) return;

    /* Decompress this frame's RLE directly to ARGB8888 */
    const uint16_t *frame_rle = &def->rle_data[def->frame_offsets[idx]];
    rle_decode_argb8888(frame_rle, s->frame_buf, w * h, TRANSPARENT_KEY);

    /* Update the LVGL image descriptor */
    s->frame_dsc.header.magic = LV_IMAGE_HEADER_MAGIC;
    s->frame_dsc.header.w = w;
    s->frame_dsc.header.h = h;
    s->frame_dsc.header.cf = LV_COLOR_FORMAT_ARGB8888;
    s->frame_dsc.header.stride = w * 4;
    s->frame_dsc.data = s->frame_buf;
    s->frame_dsc.data_size = w * h * 4;

    lv_image_set_src(s->sprite_img, &s->frame_dsc);
}
```

- [ ] **Step 5: Remove `apply_sprite_frame()` — replaced by `decode_and_apply_frame()`**

Delete the old `apply_sprite_frame()` function entirely.

- [ ] **Step 6: Update `scene_create()` — remove `build_frame_dscs` call**

In `scene_create()`, replace:

```c
    build_frame_dscs(s, &anim_defs[CLAWD_ANIM_IDLE]);
    apply_sprite_frame(s);
```

With:

```c
    decode_and_apply_frame(s);
```

- [ ] **Step 7: Update `scene_set_clawd_anim()` — remove `build_frame_dscs` call**

In `scene_set_clawd_anim()`, replace:

```c
    build_frame_dscs(scene, def);
    apply_sprite_frame(scene);
```

With:

```c
    decode_and_apply_frame(scene);
```

- [ ] **Step 8: Update `scene_tick()` — replace `apply_sprite_frame` call**

In `scene_tick()`, replace:

```c
        apply_sprite_frame(scene);
```

With:

```c
        decode_and_apply_frame(scene);
```

(The oneshot-finished branch calls `scene_set_clawd_anim()` which already calls `decode_and_apply_frame`, so only the normal frame-advance call site on line 463 needs changing.)

- [ ] **Step 9: Commit**

```bash
git add firmware/main/scene.c
git commit -m "feat: refactor scene to single-frame RLE decompression"
```

---

## Chunk 3: Regenerate Sprites + Build Verification

### Task 6: Regenerate All Sprite Headers

**Files:**
- Regenerate: `firmware/main/assets/sprite_idle.h`
- Regenerate: `firmware/main/assets/sprite_alert.h`
- Regenerate: `firmware/main/assets/sprite_happy.h`
- Regenerate: `firmware/main/assets/sprite_sleeping.h`
- Regenerate: `firmware/main/assets/sprite_disconnected.h`

The source PNGs are generated from SVGs using `tools/svg2frames.py` (Playwright + headless Chromium).
The SVGs live in `assets/svg-animations/`. Each must be rendered to a temp directory of PNG frames
before running `png2rgb565.py`.

The animation parameters below match the current sprite headers (frame counts, dimensions, fps).

- [ ] **Step 1: Render SVGs to PNG frame sequences**

```bash
# idle: 96 frames, 180x180, 8fps, 12s duration
python3 tools/svg2frames.py assets/svg-animations/clawd-idle-living.svg /tmp/frames-idle/ --fps 8 --scale 6

# alert (notification): 40 frames, 180x180, 10fps
python3 tools/svg2frames.py assets/svg-animations/clawd-notification.svg /tmp/frames-alert/ --fps 10 --scale 6

# happy: 20 frames, 160x160, 10fps
python3 tools/svg2frames.py assets/svg-animations/clawd-happy.svg /tmp/frames-happy/ --fps 10 --scale 6

# sleeping: 36 frames, 160x160, 6fps
python3 tools/svg2frames.py assets/svg-animations/clawd-sleeping.svg /tmp/frames-sleeping/ --fps 6 --scale 6

# disconnected: 36 frames, 200x160, 6fps
python3 tools/svg2frames.py assets/svg-animations/clawd-disconnected.svg /tmp/frames-disconnected/ --fps 6 --scale 6
```

Verify frame counts and dimensions match expectations for each by checking `ls /tmp/frames-*/ | wc -l`.
If dimensions or frame counts differ, adjust `--scale` and `--fps`/`--duration` flags accordingly.

- [ ] **Step 2: Regenerate each sprite header with RLE compression**

```bash
python3 tools/png2rgb565.py /tmp/frames-idle/ firmware/main/assets/sprite_idle.h --name idle
python3 tools/png2rgb565.py /tmp/frames-alert/ firmware/main/assets/sprite_alert.h --name alert
python3 tools/png2rgb565.py /tmp/frames-happy/ firmware/main/assets/sprite_happy.h --name happy
python3 tools/png2rgb565.py /tmp/frames-sleeping/ firmware/main/assets/sprite_sleeping.h --name sleeping
python3 tools/png2rgb565.py /tmp/frames-disconnected/ firmware/main/assets/sprite_disconnected.h --name disconnected
```

- [ ] **Step 3: Verify header sizes are dramatically smaller**

Run: `ls -lh firmware/main/assets/sprite_*.h`

Expected: each file should be in the KB range (not MB). Total ~300-500 KB instead of ~55 MB.

- [ ] **Step 4: Commit**

```bash
git add firmware/main/assets/sprite_*.h
git commit -m "feat: regenerate all sprites with RLE compression (13MB -> ~300KB)"
```

---

### Task 7: Build and Verify Firmware

- [ ] **Step 1: Clean and rebuild firmware**

```bash
cd firmware && idf.py fullclean && idf.py build
```

Expected: build succeeds with no linker overflow errors. The `.flash.rodata` section should now fit easily.

- [ ] **Step 2: Check binary size**

Run: `ls -lh firmware/build/clawd-tank.bin`

Expected: binary should be well under 7.9 MB (the new partition size).

- [ ] **Step 3: Build simulator**

```bash
cd simulator && cmake -B build && cmake --build build
```

Expected: simulator builds successfully — it compiles the same `scene.c` and includes the same RLE headers.

- [ ] **Step 4: Run simulator headless smoke test**

```bash
./simulator/build/clawd-tank-sim --headless \
  --events 'connect; wait 500; notify "Test" "RLE sprites"; wait 2000; disconnect' \
  --screenshot-dir ./shots/ --screenshot-on-event
```

Expected: runs without crash, generates screenshot PNGs. Visually verify sprites render correctly.

- [ ] **Step 5: Run C unit tests**

```bash
cd firmware/test && make clean && make test
```

Expected: all tests pass (notification, config_store, rle_decode).

- [ ] **Step 6: Commit any build fixes (if needed)**

Only if prior steps required adjustments.
