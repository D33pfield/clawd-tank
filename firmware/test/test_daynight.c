// firmware/test/test_daynight.c
#include <stdio.h>
#include <assert.h>
#include <stdlib.h>
#include "daynight.h"

#define HM(h, m) ((h) * 60 + (m))

/* Palette anchors, as in daynight.c. */
#define DAWN_SKY_TOP 0x2b3a67
#define DAY_SKY_TOP  0x2a78c8
#define DUSK_SKY_TOP 0x2b2d5e
#define NIGHT_SKY_TOP 0x0a0e1a

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

    daynight_palette(HM(0, 0), NULL, &p);
    assert(p.sky_top == NIGHT_SKY_TOP);
    assert(p.star_alpha == 255);
    assert(p.backlight_pct == 35);

    daynight_palette(HM(12, 0), NULL, &p);
    assert(p.sky_top == DAY_SKY_TOP);
    assert(p.star_alpha == 0);
    assert(p.backlight_pct == 100);

    /* Day is flat between 08:30 and 17:00. */
    daynight_palette_t q;
    daynight_palette(HM(9, 0), NULL, &p);
    daynight_palette(HM(16, 0), NULL, &q);
    assert(p.sky_top == q.sky_top && p.sky_bottom == q.sky_bottom);

    /* Late evening and early morning are night. */
    daynight_palette(HM(22, 0), NULL, &p);
    daynight_palette(HM(3, 0), NULL, &q);
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

    daynight_palette(HM(0, 0), NULL, &m);
    assert(n.sky_top == m.sky_top && n.sky_bottom == m.sky_bottom);
    assert(n.grass_top == m.grass_top && n.clock_text == m.clock_text);
}

static void test_wraps_around_midnight(void)
{
    daynight_palette_t a, b;
    daynight_palette(1440, NULL, &a);
    daynight_palette(0, NULL, &b);
    assert(a.sky_top == b.sky_top && a.star_alpha == b.star_alpha);

    daynight_palette(-1, NULL, &a);
    daynight_palette(1439, NULL, &b);
    assert(a.sky_top == b.sky_top && a.sky_bottom == b.sky_bottom);
}

static void assert_smooth_all_day(const daynight_sun_t *sun)
{
    /* Colors must change smoothly: at most a few levels per channel per minute. */
    daynight_palette_t prev, cur;
    daynight_palette(0, sun, &prev);
    for (int m = 1; m <= 1440; m++) {
        daynight_palette(m % 1440, sun, &cur);
        assert(chan_delta(prev.sky_top, cur.sky_top) <= 3);
        assert(chan_delta(prev.sky_bottom, cur.sky_bottom) <= 3);
        assert(chan_delta(prev.grass_top, cur.grass_top) <= 3);
        assert(chan_delta(prev.clock_text, cur.clock_text) <= 3);
        assert(abs((int)prev.star_alpha - (int)cur.star_alpha) <= 3);
        assert(abs((int)prev.backlight_pct - (int)cur.backlight_pct) <= 1);
        prev = cur;
    }
}

static void test_no_visible_jumps(void)
{
    assert_smooth_all_day(NULL);
}

static void test_backlight(void)
{
    /* Default config (102 = 40%): full at midday, dimmed at night. */
    assert(daynight_backlight(HM(12, 0), NULL, 102) == 102);
    assert(daynight_backlight(HM(0, 0), NULL, 102) == 35);

    /* User turned the backlight off: stays off at any time. */
    for (int m = 0; m < 1440; m += 30) assert(daynight_backlight(m, NULL, 0) == 0);

    /* Never above the Waveshare 50% limit, whatever is configured. */
    for (int duty = 0; duty <= 255; duty++)
        for (int m = 0; m < 1440; m += 15)
            assert(daynight_backlight(m, NULL, (uint8_t)duty) <= DAYNIGHT_BACKLIGHT_MAX_DUTY);
    assert(daynight_backlight(HM(12, 0), NULL, 255) == DAYNIGHT_BACKLIGHT_MAX_DUTY);

    /* Night is dim but not black; a tiny configured value is not raised above itself. */
    assert(daynight_backlight(HM(0, 0), NULL, 40) == 14);
    assert(daynight_backlight(HM(0, 0), NULL, 20) == 12);
    assert(daynight_backlight(HM(0, 0), NULL, 5) == 5);
}

static void test_default_sun_times(void)
{
    /* NULL means the documented defaults, and the defaults reproduce the
     * original fixed schedule exactly. */
    daynight_sun_t def = { DAYNIGHT_DEFAULT_SUNRISE, DAYNIGHT_DEFAULT_SUNSET };
    assert(def.sunrise_min == HM(6, 30) && def.sunset_min == HM(18, 30));

    daynight_palette_t a, b;
    for (int m = 0; m < 1440; m++) {
        daynight_palette(m, NULL, &a);
        daynight_palette(m, &def, &b);
        assert(a.sky_top == b.sky_top && a.sky_bottom == b.sky_bottom);
        assert(a.star_alpha == b.star_alpha && a.backlight_pct == b.backlight_pct);
    }

    /* Nonsense falls back to the defaults instead of being used. */
    daynight_sun_t bad[] = {
        { 1000, 900 },       /* sunrise after sunset */
        { 500, 500 },        /* no daylight at all */
        { -5, 1100 },        /* negative */
        { 400, 1500 },       /* past midnight */
        { 5000, 6000 },
    };
    for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
        for (int m = 0; m < 1440; m += 20) {
            daynight_palette(m, NULL, &a);
            daynight_palette(m, &bad[i], &b);
            assert(a.sky_top == b.sky_top && a.sky_bottom == b.sky_bottom);
        }
    }
}

static void test_custom_sun_times_move_the_anchors(void)
{
    /* A short midwinter day: sunrise 07:15, sunset 16:30. */
    daynight_sun_t winter = { HM(7, 15), HM(16, 30) };
    daynight_palette_t p;

    daynight_palette(winter.sunrise_min, &winter, &p);               /* dawn glow peaks at sunrise */
    assert(p.sky_top == DAWN_SKY_TOP);
    daynight_palette(winter.sunrise_min + 120, &winter, &p);         /* full day two hours later */
    assert(p.sky_top == DAY_SKY_TOP && p.star_alpha == 0);
    daynight_palette(winter.sunset_min - 90, &winter, &p);           /* day lasts until 90 min before sunset */
    assert(p.sky_top == DAY_SKY_TOP);
    daynight_palette(winter.sunset_min, &winter, &p);                /* dusk glow at sunset */
    assert(p.sky_top == DUSK_SKY_TOP);
    daynight_palette(winter.sunset_min + 120, &winter, &p);          /* dark again after two hours */
    assert(p.sky_top == NIGHT_SKY_TOP && p.star_alpha == 255);
    daynight_palette(HM(4, 0), &winter, &p);                         /* still night before the dawn lead-in */
    assert(p.sky_top == NIGHT_SKY_TOP);

    /* At 17:30 the short winter day is already past dusk; the default schedule is not. */
    daynight_palette_t def_p, win_p;
    daynight_palette(HM(17, 30), NULL, &def_p);
    daynight_palette(HM(17, 30), &winter, &win_p);
    assert(win_p.star_alpha > def_p.star_alpha);
    assert(win_p.backlight_pct < def_p.backlight_pct);

    /* A long midsummer day: sunrise 05:15, sunset 20:30. Full day holds until 19:00,
     * and at 19:30 it is far from dark, unlike the default schedule. */
    daynight_sun_t summer = { HM(5, 15), HM(20, 30) };
    daynight_palette(HM(19, 0), &summer, &p);
    assert(p.sky_top == DAY_SKY_TOP && p.star_alpha == 0);
    daynight_palette(HM(19, 30), &summer, &p);
    daynight_palette(HM(19, 30), NULL, &def_p);
    assert(p.star_alpha < def_p.star_alpha);
    assert(p.backlight_pct > def_p.backlight_pct);

    /* The backlight follows the same anchors: dawn level (70%) at sunrise. */
    assert(daynight_backlight(winter.sunrise_min, &winter, 102) == 102 * 70 / 100);
}

static void test_smooth_for_realistic_days(void)
{
    /* Every plausible sunrise/sunset pair keeps the fades smooth. */
    for (int rise = HM(3, 0); rise <= HM(9, 0); rise += 30) {
        for (int set = HM(15, 30); set <= HM(22, 0); set += 30) {
            daynight_sun_t sun = { rise, set };
            assert_smooth_all_day(&sun);
        }
    }
}

static void test_robust_for_any_sun_times(void)
{
    /* Whatever arrives (valid, inverted, huge, negative, polar-short or
     * polar-long days), every minute must produce a sane palette and no division
     * by zero or overflow (run under ASan/UBSan). */
    for (int rise = -100; rise <= 1600; rise += 37) {
        for (int set = -100; set <= 1600; set += 41) {
            daynight_sun_t sun = { rise, set };
            for (int m = -3; m < 1445; m += 7) {
                daynight_palette_t p;
                daynight_palette(m, &sun, &p);
                assert(p.backlight_pct <= 100);
                assert(p.sky_top <= 0xFFFFFF && p.sky_bottom <= 0xFFFFFF);
                assert(daynight_backlight(m, &sun, 200) <= DAYNIGHT_BACKLIGHT_MAX_DUTY);
            }
        }
    }

    /* The extremes the host can actually send: a 2.5 h polar day and a 23 h midnight-sun day. */
    daynight_sun_t shortest = { HM(10, 45), HM(13, 15) };
    daynight_sun_t longest = { HM(0, 30), HM(23, 30) };
    for (int m = 0; m < 1440; m++) {
        daynight_palette_t p;
        daynight_palette(m, &shortest, &p);
        daynight_palette(m, &longest, &p);
    }
}

int main(void)
{
    test_night_and_day_anchors();
    test_night_palette_is_the_original_scene();
    test_wraps_around_midnight();
    test_no_visible_jumps();
    test_backlight();
    test_default_sun_times();
    test_custom_sun_times_move_the_anchors();
    test_smooth_for_realistic_days();
    test_robust_for_any_sun_times();
    printf("test_daynight: all tests passed\n");
    return 0;
}
