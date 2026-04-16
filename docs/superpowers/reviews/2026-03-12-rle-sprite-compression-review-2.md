# Plan Review: RLE Sprite Compression (Round 2)

**Plan:** `docs/superpowers/plans/2026-03-12-rle-sprite-compression.md`
**Date:** 2026-03-12
**Status:** Approved

---

## Verification of the Three Previously Reported Fixes

### Fix 1 — Task 6 now includes svg2frames.py pre-step

Confirmed correct. Task 6 Step 1 renders all five SVGs with parameters that match
the existing sprite headers exactly:

- idle: 96 frames at 8fps × 12s, 180x180 — matches `IDLE_FRAME_COUNT=96`, `IDLE_WIDTH=180`
- alert: 40 frames at 10fps, 180x180 — matches `ALERT_FRAME_COUNT=40`, `ALERT_WIDTH=180`
- happy: 20 frames at 10fps, 160x160 — matches `HAPPY_FRAME_COUNT=20`, `HAPPY_WIDTH=160`
- sleeping: 36 frames at 6fps, 160x160 — matches `SLEEPING_FRAME_COUNT=36`, `SLEEPING_WIDTH=160`
- disconnected: 36 frames at 6fps, 200x160 — matches `DISCONNECTED_FRAME_COUNT=36`, `DISCONNECTED_WIDTH=200`

SVG paths in the commands correspond exactly to the files present in
`assets/svg-animations/` (clawd-idle-living.svg, clawd-notification.svg,
clawd-happy.svg, clawd-sleeping.svg, clawd-disconnected.svg — all confirmed
present). The `--scale 6` flag matches the `scale=6.0` default in
`tools/svg2frames.py`. Fix is correct.

### Fix 2 — Task 5 Step 8 comment no longer says "two places"

Confirmed. The comment now reads:

> (The oneshot-finished branch calls `scene_set_clawd_anim()` which already calls
> `decode_and_apply_frame`, so only the normal frame-advance call site on line 463
> needs changing.)

This is accurate. `scene.c` has `apply_sprite_frame(scene)` at line 463 (inside
the normal frame-advance branch of `scene_tick`), and the oneshot path at line 459
calls `scene_set_clawd_anim()`, which in the refactored version will call
`decode_and_apply_frame` itself. The single call-site instruction is correct.
Fix is correct.

### Fix 3 — Task 4 Step 3 no longer has a placeholder cd path

Confirmed. The roundtrip test in Step 3 uses `sys.path.insert(0, 'tools')` and is
invoked as `python3 -c "..."` from the repo root with no `cd` command. Fix is correct.

---

## Remaining Issues Check

No new issues were found. Cross-checking the plan against the codebase produced the
following confirmations:

**svg2frames.py flags** — `--fps`, `--duration` (via auto-detect), `--scale` all
match the actual CLI parser in `tools/svg2frames.py`. The idle invocation comment
says "96 frames, 8fps, 12s duration" and `96 = round(12 * 8)` is correct.

**Scene struct field names** — The plan instructs removing `frame_dscs[96]` and
`frame_bufs[96]` and replacing them with `frame_dsc`, `frame_buf`, and
`frame_buf_size`. These names match the replacement helper functions
`ensure_frame_buf` and `decode_and_apply_frame` in the plan. Consistent.

**TRANSPARENT_KEY in decode_and_apply_frame** — The plan's snippet passes
`TRANSPARENT_KEY` (the C macro defined as `0x18C5` in `scene.c`) to
`rle_decode_argb8888`. That macro is already present in `scene.c` and will remain
after the refactor. No issue.

**scene.c call-site count** — The plan correctly accounts for all three call sites
in the pre-refactor code: `scene_create` (line 289), `scene_set_clawd_anim`
(line 394), and `scene_tick` (line 463). Steps 6, 7, and 8 cover each one. Correct.

**Test 4 (frame offset indexing)** — The inline comment block in the test is
verbose and slightly self-contradictory mid-comment, but the final offsets array
used in the actual assertions (`{0, 2, 6}`) is arithmetically correct: frame 0 has
1 run pair = 2 uint16_t words starting at offset 0; frame 1 has 2 run pairs = 4
uint16_t words starting at offset 2; sentinel at offset 6. The test code itself is
correct regardless of the messy comment prose. Not a plan defect.

**Makefile update** — The `.PHONY` line is currently `.PHONY: test clean` in the
existing Makefile. The plan does not show updating `.PHONY` to add
`test_rle_decode`. This is a minor omission — without it, `make test_rle_decode`
still works (the target has no phony semantics needed), and `make test` and
`make clean` are already covered. Not a blocking issue.

---

## Result

All three previously reported issues are correctly fixed. No new blocking issues
were found. The plan is ready for implementation.
