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

/* Palette for a local time given as minutes since midnight (0-1439).
 * Blends smoothly between night, dawn, day and dusk. Out-of-range values wrap. */
void daynight_palette(int minute_of_day, daynight_palette_t *out);

/* Backlight duty (0-255) for the time of day, scaled from the user's configured
 * brightness. Never exceeds DAYNIGHT_BACKLIGHT_MAX_DUTY, and a configured value
 * of 0 stays 0. */
uint8_t daynight_backlight(int minute_of_day, uint8_t configured_duty);

/* Waveshare: keep the screen at 50% brightness or lower to avoid overheating. */
#define DAYNIGHT_BACKLIGHT_MAX_DUTY 127
