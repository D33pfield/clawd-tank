// firmware/main/daynight.c
#include "daynight.h"
#include <stddef.h>

#define MINUTES_PER_DAY      1440
#define BACKLIGHT_FLOOR_DUTY 12   /* never fully dark while awake (sleep turns it off separately) */

#define HM(h, m) ((h) * 60 + (m))

typedef struct {
    int minute;
    daynight_palette_t p;
} keyframe_t;

/* Palettes at fixed times of day; everything in between is interpolated.
 * The timeline must start at 0 and end at 1440 with the same look (night). */
#define NIGHT { 0x0a0e1a, 0x1a1a2e, 0x2d4a2d, 0x1a331a, 0x3d6a3d, 0x4466aa, 255,  35 }
static const keyframe_t s_keys[] = {
    { HM( 0,  0), NIGHT },
    { HM( 5,  0), NIGHT },
    { HM( 6, 30), { 0x2b3a67, 0xe8946a, 0x355a35, 0x233f23, 0x4a7a4a, 0xf2d9b0,  80,  70 } },  /* dawn */
    { HM( 8, 30), { 0x2a78c8, 0xa9dcf7, 0x4fa84f, 0x3a853a, 0x6cc46c, 0xffffff,   0, 100 } },  /* day  */
    { HM(17,  0), { 0x2a78c8, 0xa9dcf7, 0x4fa84f, 0x3a853a, 0x6cc46c, 0xffffff,   0, 100 } },
    { HM(18, 30), { 0x2b2d5e, 0xe0704f, 0x35553a, 0x233f27, 0x4a7a4a, 0xf2c9a0,  90,  65 } },  /* dusk */
    { HM(20, 30), NIGHT },
    { HM(24,  0), NIGHT },
};
#define KEY_COUNT (sizeof(s_keys) / sizeof(s_keys[0]))

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
    static const daynight_palette_t night = NIGHT;
    *out = night;
}

void daynight_palette(int minute_of_day, daynight_palette_t *out)
{
    int minute = ((minute_of_day % MINUTES_PER_DAY) + MINUTES_PER_DAY) % MINUTES_PER_DAY;

    /* Find the keyframe segment [i, i+1] containing this minute. */
    size_t i = 0;
    while (i + 2 < KEY_COUNT && minute >= s_keys[i + 1].minute) i++;

    const daynight_palette_t *a = &s_keys[i].p;
    const daynight_palette_t *b = &s_keys[i + 1].p;
    int num = minute - s_keys[i].minute;
    int den = s_keys[i + 1].minute - s_keys[i].minute;

    out->sky_top       = mix_rgb(a->sky_top,       b->sky_top,       num, den);
    out->sky_bottom    = mix_rgb(a->sky_bottom,    b->sky_bottom,    num, den);
    out->grass_top     = mix_rgb(a->grass_top,     b->grass_top,     num, den);
    out->grass_bottom  = mix_rgb(a->grass_bottom,  b->grass_bottom,  num, den);
    out->tuft          = mix_rgb(a->tuft,          b->tuft,          num, den);
    out->clock_text    = mix_rgb(a->clock_text,    b->clock_text,    num, den);
    out->star_alpha    = mix_u8(a->star_alpha,     b->star_alpha,    num, den);
    out->backlight_pct = mix_u8(a->backlight_pct,  b->backlight_pct, num, den);
}

uint8_t daynight_backlight(int minute_of_day, uint8_t configured_duty)
{
    if (configured_duty == 0) return 0;

    daynight_palette_t p;
    daynight_palette(minute_of_day, &p);

    int duty = configured_duty * p.backlight_pct / 100;
    int floor = configured_duty < BACKLIGHT_FLOOR_DUTY ? configured_duty : BACKLIGHT_FLOOR_DUTY;
    if (duty < floor) duty = floor;
    if (duty > DAYNIGHT_BACKLIGHT_MAX_DUTY) duty = DAYNIGHT_BACKLIGHT_MAX_DUTY;
    return (uint8_t)duty;
}
