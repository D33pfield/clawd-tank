// firmware/main/config_store.c
#include "config_store.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "nvs.h"
#include <stdio.h>
#include <string.h>

static const char *TAG = "config";
static const char *NVS_NAMESPACE = "clawd_cfg";

static uint8_t  s_brightness;
static uint16_t s_sleep_timeout_secs;
static bool     s_day_night;

void config_store_init(void)
{
    s_brightness = CONFIG_DEFAULT_BRIGHTNESS;
    s_sleep_timeout_secs = CONFIG_DEFAULT_SLEEP_TIMEOUT;
    s_day_night = false;

    nvs_handle_t handle;
    esp_err_t err = nvs_open(NVS_NAMESPACE, 1 /* NVS_READWRITE */, &handle);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "NVS open failed (%d), using defaults", err);
        return;
    }

    uint8_t b;
    if (nvs_get_u8(handle, "brightness", &b) == ESP_OK) {
        s_brightness = b;
        ESP_LOGI(TAG, "Loaded brightness=%u from NVS", b);
    }

    uint16_t t;
    if (nvs_get_u16(handle, "sleep_tmout", &t) == ESP_OK) {
        s_sleep_timeout_secs = t;
        ESP_LOGI(TAG, "Loaded sleep_timeout=%u from NVS", t);
    }

    uint8_t dn;
    if (nvs_get_u8(handle, "day_night", &dn) == ESP_OK) {
        s_day_night = dn != 0;
        ESP_LOGI(TAG, "Loaded day_night=%u from NVS", dn);
    }

    nvs_close(handle);
}

uint8_t config_store_get_brightness(void)
{
    return s_brightness;
}

uint32_t config_store_get_sleep_timeout_ms(void)
{
    return (uint32_t)s_sleep_timeout_secs * 1000;
}

bool config_store_get_day_night(void)
{
    return s_day_night;
}

void config_store_set_day_night(bool enabled)
{
    s_day_night = enabled;

    nvs_handle_t handle;
    if (nvs_open(NVS_NAMESPACE, 1, &handle) == ESP_OK) {
        nvs_set_u8(handle, "day_night", enabled ? 1 : 0);
        nvs_commit(handle);
        nvs_close(handle);
    }
}

void config_store_set_brightness(uint8_t duty)
{
    s_brightness = duty;

    nvs_handle_t handle;
    if (nvs_open(NVS_NAMESPACE, 1, &handle) == ESP_OK) {
        nvs_set_u8(handle, "brightness", duty);
        nvs_commit(handle);
        nvs_close(handle);
    }
}

void config_store_set_sleep_timeout(uint16_t seconds)
{
    s_sleep_timeout_secs = seconds;

    nvs_handle_t handle;
    if (nvs_open(NVS_NAMESPACE, 1, &handle) == ESP_OK) {
        nvs_set_u16(handle, "sleep_tmout", seconds);
        nvs_commit(handle);
        nvs_close(handle);
    }
}

uint16_t config_store_serialize_json(char *buf, uint16_t buf_sz)
{
    int n = snprintf(buf, buf_sz,
        "{\"brightness\":%u,\"sleep_timeout\":%u,\"day_night\":%s}",
        s_brightness, s_sleep_timeout_secs, s_day_night ? "true" : "false");
    if (n < 0 || (uint16_t)n >= buf_sz) {
        if (buf_sz > 0) buf[0] = '\0';
        return 0;
    }
    return (uint16_t)n;
}
