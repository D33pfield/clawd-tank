// firmware/test/test_daynight.c
#include <stdio.h>
#include <assert.h>
#include <stdlib.h>
#include "daynight.h"

#define HM(h, m) ((h) * 60 + (m))

static int chan_delta(uint32_t a, uint32_t b)
{
    int d = 0;
    for (int shift = 16; shift >= 0; shift -= 8) {
        int x = (int)((a >> shift) & 0xFF) - (int)((b >> shift) & 0xFF);
        if (x < 0) x = -x;
        if (x > d) d = x;
    }
    return d;
}

static void test_night_and_day_anchors(void)
{
    daynight_palette_t p;

    daynight_palette(HM(0, 0), &p);
    assert(p.sky_top == 0x0a0e1a);
    assert(p.star_alpha == 255);
    assert(p.backlight_pct == 35);

    daynight_palette(HM(12, 0), &p);
    assert(p.sky_top == 0x2a78c8);
    assert(p.star_alpha == 0);
    assert(p.backlight_pct == 100);

    /* Day is flat between 08:30 and 17:00. */
    daynight_palette_t q;
    daynight_palette(HM(9, 0), &p);
    daynight_palette(HM(16, 0), &q);
    assert(p.sky_top == q.sky_top && p.sky_bottom == q.sky_bottom);

    /* Late evening and early morning are night. */
    daynight_palette(HM(22, 0), &p);
    daynight_palette(HM(3, 0), &q);
    assert(p.sky_top == q.sky_top && p.star_alpha == 255);
}

static void test_night_palette_is_the_original_scene(void)
{
    /* The fixed night look used when the cycle is off must stay the scene's
     * original colors, and match the cycle's own midnight. */
    daynight_palette_t n, m;
    daynight_night_palette(&n);
    assert(n.sky_top == 0x0a0e1a && n.sky_bottom == 0x1a1a2e);
    assert(n.grass_top == 0x2d4a2d && n.grass_bottom == 0x1a331a);
    assert(n.tuft == 0x3d6a3d && n.clock_text == 0x4466aa);
    assert(n.star_alpha == 255);

    daynight_palette(HM(0, 0), &m);
    assert(n.sky_top == m.sky_top && n.sky_bottom == m.sky_bottom);
    assert(n.grass_top == m.grass_top && n.clock_text == m.clock_text);
}

static void test_wraps_around_midnight(void)
{
    daynight_palette_t a, b;
    daynight_palette(1440, &a);
    daynight_palette(0, &b);
    assert(a.sky_top == b.sky_top && a.star_alpha == b.star_alpha);

    daynight_palette(-1, &a);
    daynight_palette(1439, &b);
    assert(a.sky_top == b.sky_top && a.sky_bottom == b.sky_bottom);
}

static void test_no_visible_jumps(void)
{
    /* Colors must change smoothly: at most a few levels per channel per minute. */
    daynight_palette_t prev, cur;
    daynight_palette(0, &prev);
    for (int m = 1; m <= 1440; m++) {
        daynight_palette(m % 1440, &cur);
        assert(chan_delta(prev.sky_top, cur.sky_top) <= 3);
        assert(chan_delta(prev.sky_bottom, cur.sky_bottom) <= 3);
        assert(chan_delta(prev.grass_top, cur.grass_top) <= 3);
        assert(chan_delta(prev.clock_text, cur.clock_text) <= 3);
        assert(abs((int)prev.star_alpha - (int)cur.star_alpha) <= 3);
        assert(abs((int)prev.backlight_pct - (int)cur.backlight_pct) <= 1);
        prev = cur;
    }
}

static void test_backlight(void)
{
    /* Default config (102 = 40%): full at midday, dimmed at night. */
    assert(daynight_backlight(HM(12, 0), 102) == 102);
    assert(daynight_backlight(HM(0, 0), 102) == 35);

    /* User turned the backlight off: stays off at any time. */
    for (int m = 0; m < 1440; m += 30) assert(daynight_backlight(m, 0) == 0);

    /* Never above the Waveshare 50% limit, whatever is configured. */
    for (int duty = 0; duty <= 255; duty++)
        for (int m = 0; m < 1440; m += 15)
            assert(daynight_backlight(m, (uint8_t)duty) <= DAYNIGHT_BACKLIGHT_MAX_DUTY);
    assert(daynight_backlight(HM(12, 0), 255) == DAYNIGHT_BACKLIGHT_MAX_DUTY);

    /* Night is dim but not black; a tiny configured value is not raised above itself. */
    assert(daynight_backlight(HM(0, 0), 40) == 14);
    assert(daynight_backlight(HM(0, 0), 20) == 12);
    assert(daynight_backlight(HM(0, 0), 5) == 5);
}

int main(void)
{
    test_night_and_day_anchors();
    test_night_palette_is_the_original_scene();
    test_wraps_around_midnight();
    test_no_visible_jumps();
    test_backlight();
    printf("test_daynight: all tests passed\n");
    return 0;
}
