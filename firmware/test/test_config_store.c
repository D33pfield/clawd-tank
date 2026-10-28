// firmware/test/test_config_store.c
#include <stdio.h>
#include <string.h>
#include <assert.h>
#include <stdlib.h>

// Pull in stub headers that satisfy config_store.c's ESP-IDF includes.
// These stubs live in firmware/test/stubs/ and are on the include path
// via -I../test/stubs in the Makefile.
#include "nvs.h"

// --- NVS mock ---
// Types and ESP_OK/ESP_ERR_NVS_NOT_FOUND come from nvs.h stub above.

static uint8_t  mock_brightness = 0;
static uint16_t mock_sleep_timeout = 0;
static int mock_brightness_set = 0;
static int mock_sleep_timeout_set = 0;
static uint8_t mock_day_night = 0;
static int mock_day_night_set = 0;

static void mock_nvs_reset(void) {
    mock_brightness = 0;
    mock_sleep_timeout = 0;
    mock_brightness_set = 0;
    mock_sleep_timeout_set = 0;
    mock_day_night = 0;
    mock_day_night_set = 0;
}

esp_err_t nvs_open(const char *ns, int mode, nvs_handle_t *handle) {
    (void)ns; (void)mode;
    *handle = 1;
    return ESP_OK;
}

esp_err_t nvs_get_u8(nvs_handle_t h, const char *key, uint8_t *val) {
    (void)h;
    if (strcmp(key, "brightness") == 0 && mock_brightness_set) {
        *val = mock_brightness;
        return ESP_OK;
    }
    if (strcmp(key, "day_night") == 0 && mock_day_night_set) {
        *val = mock_day_night;
        return ESP_OK;
    }
    return ESP_ERR_NVS_NOT_FOUND;
}

esp_err_t nvs_get_u16(nvs_handle_t h, const char *key, uint16_t *val) {
    (void)h;
    if (strcmp(key, "sleep_tmout") == 0 && mock_sleep_timeout_set) {
        *val = mock_sleep_timeout;
        return ESP_OK;
    }
    return ESP_ERR_NVS_NOT_FOUND;
}

esp_err_t nvs_set_u8(nvs_handle_t h, const char *key, uint8_t val) {
    (void)h;
    if (strcmp(key, "brightness") == 0) {
        mock_brightness = val;
        mock_brightness_set = 1;
    }
    if (strcmp(key, "day_night") == 0) {
        mock_day_night = val;
        mock_day_night_set = 1;
    }
    return ESP_OK;
}

esp_err_t nvs_set_u16(nvs_handle_t h, const char *key, uint16_t val) {
    (void)h;
    if (strcmp(key, "sleep_tmout") == 0) {
        mock_sleep_timeout = val;
        mock_sleep_timeout_set = 1;
    }
    return ESP_OK;
}

esp_err_t nvs_commit(nvs_handle_t h) { (void)h; return ESP_OK; }
void nvs_close(nvs_handle_t h) { (void)h; }

// Now include the implementation directly (compiles with our mocks)
#include "../main/config_store.c"

// --- Tests ---

static void test_defaults_when_nvs_empty(void) {
    mock_nvs_reset();
    config_store_init();
    assert(config_store_get_brightness() == CONFIG_DEFAULT_BRIGHTNESS);
    assert(config_store_get_sleep_timeout_ms() == CONFIG_DEFAULT_SLEEP_TIMEOUT * 1000);
    printf("  PASS: test_defaults_when_nvs_empty\n");
}

static void test_loads_from_nvs(void) {
    mock_nvs_reset();
    mock_brightness = 200;
    mock_brightness_set = 1;
    mock_sleep_timeout = 60;
    mock_sleep_timeout_set = 1;
    config_store_init();
    assert(config_store_get_brightness() == 200);
    assert(config_store_get_sleep_timeout_ms() == 60000);
    printf("  PASS: test_loads_from_nvs\n");
}

static void test_set_brightness_persists(void) {
    mock_nvs_reset();
    config_store_init();
    config_store_set_brightness(255);
    assert(config_store_get_brightness() == 255);
    assert(mock_brightness == 255);
    assert(mock_brightness_set == 1);
    printf("  PASS: test_set_brightness_persists\n");
}

static void test_set_sleep_timeout_persists(void) {
    mock_nvs_reset();
    config_store_init();
    config_store_set_sleep_timeout(120);
    assert(config_store_get_sleep_timeout_ms() == 120000);
    assert(mock_sleep_timeout == 120);
    assert(mock_sleep_timeout_set == 1);
    printf("  PASS: test_set_sleep_timeout_persists\n");
}

static void test_serialize_json(void) {
    mock_nvs_reset();
    config_store_init();
    config_store_set_brightness(128);
    config_store_set_sleep_timeout(600);

    char buf[256];
    uint16_t len = config_store_serialize_json(buf, sizeof(buf));
    assert(len > 0);
    assert(len == strlen(buf));

    // Verify it contains expected fields
    assert(strstr(buf, "\"brightness\":128") != NULL);
    assert(strstr(buf, "\"sleep_timeout\":600") != NULL);
    printf("  PASS: test_serialize_json\n");
}

static void test_serialize_json_default_values(void) {
    mock_nvs_reset();
    config_store_init();

    char buf[256];
    uint16_t len = config_store_serialize_json(buf, sizeof(buf));
    assert(len > 0);
    assert(strstr(buf, "\"brightness\":102") != NULL);
    assert(strstr(buf, "\"sleep_timeout\":300") != NULL);
    printf("  PASS: test_serialize_json_default_values\n");
}

static void test_day_night_defaults_off(void) {
    mock_nvs_reset();
    config_store_init();
    assert(config_store_get_day_night() == false);

    char buf[256];
    config_store_serialize_json(buf, sizeof(buf));
    assert(strstr(buf, "\"day_night\":false") != NULL);
    printf("  PASS: test_day_night_defaults_off\n");
}

static void test_day_night_persists_and_reloads(void) {
    mock_nvs_reset();
    config_store_init();
    config_store_set_day_night(true);
    assert(config_store_get_day_night() == true);
    assert(mock_day_night == 1);
    assert(mock_day_night_set == 1);

    char buf[256];
    config_store_serialize_json(buf, sizeof(buf));
    assert(strstr(buf, "\"day_night\":true") != NULL);

    // A fresh boot reads it back from NVS.
    config_store_init();
    assert(config_store_get_day_night() == true);

    config_store_set_day_night(false);
    assert(mock_day_night == 0);
    config_store_init();
    assert(config_store_get_day_night() == false);
    printf("  PASS: test_day_night_persists_and_reloads\n");
}

static void test_serialize_json_small_buffer(void) {
    mock_nvs_reset();
    config_store_init();

    char buf[10];  // Too small for full JSON
    uint16_t len = config_store_serialize_json(buf, sizeof(buf));
    // Should return 0 or truncated safely
    assert(len < sizeof(buf));
    printf("  PASS: test_serialize_json_small_buffer\n");
}

int main(void) {
    printf("Running config store tests...\n");
    test_defaults_when_nvs_empty();
    test_loads_from_nvs();
    test_set_brightness_persists();
    test_set_sleep_timeout_persists();
    test_serialize_json();
    test_serialize_json_default_values();
    test_day_night_defaults_off();
    test_day_night_persists_and_reloads();
    test_serialize_json_small_buffer();
    printf("All config store tests passed!\n");
    return 0;
}
