// firmware/main/daynight.c
#include "daynight.h"
#include <stddef.h>

#define MINUTES_PER_DAY      1440
#define BACKLIGHT_FLOOR_DUTY 12   /* never fully dark while awake (sleep turns it off separately) */

/* Fade lengths around the sun times, in minutes. The dawn glow builds for
 * DAWN_LEAD before sunrise, full day is reached DAY_RAMP after it; the day
 * starts to cool DUSK_LEAD before sunset and is fully dark NIGHT_RAMP after it.
 * With the default 06:30 / 18:30 this gives 05:00, 08:30, 17:00 and 20:30. */
#define DAWN_LEAD  90
#define DAY_RAMP   120
#define DUSK_LEAD  90
#define NIGHT_RAMP 120

/* Palettes at the anchor times; everything in between is interpolated. */
#define NIGHT { 0x0a0e1a, 0x1a1a2e, 0x2d4a2d, 0x1a331a, 0x3d6a3d, 0x4466aa, 255,  35 }
static const daynight_palette_t k_night = NIGHT;
static const daynight_palette_t k_dawn  = { 0x2b3a67, 0xe8946a, 0x355a35, 0x233f23, 0x4a7a4a, 0xf2d9b0,  80,  70 };
static const daynight_palette_t k_day   = { 0x2a78c8, 0xa9dcf7, 0x4fa84f, 0x3a853a, 0x6cc46c, 0xffffff,   0, 100 };
static const daynight_palette_t k_dusk  = { 0x2b2d5e, 0xe0704f, 0x35553a, 0x233f27, 0x4a7a4a, 0xf2c9a0,  90,  65 };

typedef struct {
    int minute;
    const daynight_palette_t *p;
} keyframe_t;
#define KEY_COUNT 8

static void build_keys(const daynight_sun_t *sun, keyframe_t keys[KEY_COUNT])
{
    int rise = DAYNIGHT_DEFAULT_SUNRISE;
    int set = DAYNIGHT_DEFAULT_SUNSET;
    if (sun && sun->sunrise_min >= 0 && sun->sunrise_min < sun->sunset_min &&
        sun->sunset_min <= MINUTES_PER_DAY) {
        rise = sun->sunrise_min;
        set = sun->sunset_min;
    }

    keys[0] = (keyframe_t){ 0,                &k_night };
    keys[1] = (keyframe_t){ rise - DAWN_LEAD, &k_night };
    keys[2] = (keyframe_t){ rise,             &k_dawn  };
    keys[3] = (keyframe_t){ rise + DAY_RAMP,  &k_day   };
    keys[4] = (keyframe_t){ set - DUSK_LEAD,  &k_day   };
    keys[5] = (keyframe_t){ set,              &k_dusk  };
    keys[6] = (keyframe_t){ set + NIGHT_RAMP, &k_night };
    keys[7] = (keyframe_t){ MINUTES_PER_DAY,  &k_night };

    /* Short winter days or midnight sun can push keys past each other or off the
     * ends of the day. Squeeze the middle ones into (0, 1440) in strictly
     * increasing order so every segment has a positive length. */
    for (int i = 1; i < KEY_COUNT - 1; i++) {
        if (keys[i].minute <= keys[i - 1].minute) keys[i].minute = keys[i - 1].minute + 1;
    }
    for (int i = KEY_COUNT - 2; i >= 1; i--) {
        if (keys[i].minute >= keys[i + 1].minute) keys[i].minute = keys[i + 1].minute - 1;
    }
}

static uint32_t mix_rgb(uint32_t a, uint32_t b, int num, int den)
{
    uint32_t out = 0;
    for (int shift = 16; shift >= 0; shift -= 8) {
        int ca = (int)((a >> shift) & 0xFF);
        int cb = (int)((b >> shift) & 0xFF);
        out |= (uint32_t)(ca + (cb - ca) * num / den) << shift;
    }
    return out;
}

static uint8_t mix_u8(uint8_t a, uint8_t b, int num, int den)
{
    return (uint8_t)(a + ((int)b - (int)a) * num / den);
}

void daynight_night_palette(daynight_palette_t *out)
{
    *out = k_night;
}

void daynight_palette(int minute_of_day, const daynight_sun_t *sun, daynight_palette_t *out)
{
    int minute = ((minute_of_day % MINUTES_PER_DAY) + MINUTES_PER_DAY) % MINUTES_PER_DAY;

    keyframe_t keys[KEY_COUNT];
    build_keys(sun, keys);

    /* Find the keyframe segment [i, i+1] containing this minute. */
    int i = 0;
    while (i + 2 < KEY_COUNT && minute >= keys[i + 1].minute) i++;

    const daynight_palette_t *a = keys[i].p;
    const daynight_palette_t *b = keys[i + 1].p;
    int num = minute - keys[i].minute;
    int den = keys[i + 1].minute - keys[i].minute;

    out->sky_top       = mix_rgb(a->sky_top,       b->sky_top,       num, den);
    out->sky_bottom    = mix_rgb(a->sky_bottom,    b->sky_bottom,    num, den);
    out->grass_top     = mix_rgb(a->grass_top,     b->grass_top,     num, den);
    out->grass_bottom  = mix_rgb(a->grass_bottom,  b->grass_bottom,  num, den);
    out->tuft          = mix_rgb(a->tuft,          b->tuft,          num, den);
    out->clock_text    = mix_rgb(a->clock_text,    b->clock_text,    num, den);
    out->star_alpha    = mix_u8(a->star_alpha,     b->star_alpha,    num, den);
    out->backlight_pct = mix_u8(a->backlight_pct,  b->backlight_pct, num, den);
}

uint8_t daynight_backlight(int minute_of_day, const daynight_sun_t *sun, uint8_t configured_duty)
{
    if (configured_duty == 0) return 0;

    daynight_palette_t p;
    daynight_palette(minute_of_day, sun, &p);

    int duty = configured_duty * p.backlight_pct / 100;
    int floor = configured_duty < BACKLIGHT_FLOOR_DUTY ? configured_duty : BACKLIGHT_FLOOR_DUTY;
    if (duty < floor) duty = floor;
    if (duty > DAYNIGHT_BACKLIGHT_MAX_DUTY) duty = DAYNIGHT_BACKLIGHT_MAX_DUTY;
    return (uint8_t)duty;
}
