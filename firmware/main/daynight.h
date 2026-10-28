// firmware/main/daynight.h
// Time-of-day look for the scene: sky/grass colors, star visibility and backlight.
// Pure functions (no LVGL / ESP-IDF) so they can be unit tested on the host.
#pragma once
#include <stdint.h>

typedef struct {
    uint32_t sky_top;        /* 0xRRGGBB */
    uint32_t sky_bottom;
    uint32_t grass_top;
    uint32_t grass_bottom;
    uint32_t tuft;
    uint32_t clock_text;
    uint8_t  star_alpha;     /* 0 = hidden (daytime), 255 = full (night) */
    uint8_t  backlight_pct;  /* share of the configured brightness, 0-100 */
} daynight_palette_t;

/* Today's sunrise and sunset in local minutes since midnight. The host computes
 * them (they shift through the year) and sends them with set_time. */
typedef struct {
    int sunrise_min;
    int sunset_min;
} daynight_sun_t;

/* Used when the host sends nothing, or sends values that don't make sense. */
#define DAYNIGHT_DEFAULT_SUNRISE (6 * 60 + 30)
#define DAYNIGHT_DEFAULT_SUNSET  (18 * 60 + 30)

/* Palette for a local time given as minutes since midnight (0-1439).
 * Blends smoothly between night, dawn (ending at sunrise), day, dusk (at sunset)
 * and night. Out-of-range minutes wrap. `sun` may be NULL for the defaults;
 * invalid values (sunrise not before sunset, or outside 0-1440) also fall back
 * to them. A sunrise before 01:30 or a sunset after 22:00 is treated as 01:30 /
 * 22:00 so each fade fits inside one day (midnight-sun days would otherwise snap
 * at midnight). Very short days are squeezed so the timeline always stays ordered. */
void daynight_palette(int minute_of_day, const daynight_sun_t *sun, daynight_palette_t *out);

/* The fixed night look (the scene's original palette), used when the day/night
 * cycle is turned off. Same as daynight_palette() at midnight. */
void daynight_night_palette(daynight_palette_t *out);

/* Backlight duty (0-255) for the time of day, scaled from the user's configured
 * brightness. Never exceeds DAYNIGHT_BACKLIGHT_MAX_DUTY, and a configured value
 * of 0 stays 0. `sun` as for daynight_palette(). */
uint8_t daynight_backlight(int minute_of_day, const daynight_sun_t *sun, uint8_t configured_duty);

/* Waveshare: keep the screen at 50% brightness or lower to avoid overheating. */
#define DAYNIGHT_BACKLIGHT_MAX_DUTY 127
