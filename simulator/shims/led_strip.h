/* Simulator shim — LED strip with color capture for RGB LED simulation */
#pragma once
#include <stdint.h>

#ifndef ESP_OK
#define ESP_OK 0
#endif

typedef int esp_err_t;
typedef void *led_strip_handle_t;

typedef enum { LED_PIXEL_FORMAT_GRB = 0 } led_pixel_format_t;
typedef enum { LED_MODEL_WS2812 = 0 } led_model_t;
typedef enum { RMT_CLK_SRC_DEFAULT = 0 } rmt_clock_source_t;

typedef struct {
    int strip_gpio_num;
    uint32_t max_leds;
    led_pixel_format_t led_pixel_format;
    led_model_t led_model;
    struct { uint32_t invert_out; } flags;
} led_strip_config_t;

typedef struct {
    rmt_clock_source_t clk_src;
    uint32_t resolution_hz;
    struct { int with_dma; } flags;
} led_strip_rmt_config_t;

/* Pending color (set_pixel stores, refresh commits) */
static uint8_t s_pending_r, s_pending_g, s_pending_b;

/* Defined in sim_display.c — updates the visible border color */
extern void sim_rgb_led_update(uint8_t r, uint8_t g, uint8_t b);

static inline esp_err_t led_strip_new_rmt_device(const led_strip_config_t *c,
                                                  const led_strip_rmt_config_t *r,
                                                  led_strip_handle_t *h)
{
    (void)c; (void)r;
    *h = (void *)1;
    return ESP_OK;
}

static inline esp_err_t led_strip_set_pixel(led_strip_handle_t h, int i, uint8_t r, uint8_t g, uint8_t b)
{
    (void)h; (void)i;
    s_pending_r = r;
    s_pending_g = g;
    s_pending_b = b;
    return ESP_OK;
}

static inline esp_err_t led_strip_refresh(led_strip_handle_t h)
{
    (void)h;
    sim_rgb_led_update(s_pending_r, s_pending_g, s_pending_b);
    return ESP_OK;
}

static inline esp_err_t led_strip_clear(led_strip_handle_t h)
{
    (void)h;
    sim_rgb_led_update(0, 0, 0);
    return ESP_OK;
}
