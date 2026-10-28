// firmware/main/config_store.h
#ifndef CONFIG_STORE_H
#define CONFIG_STORE_H

#include <stdbool.h>
#include <stdint.h>

#define CONFIG_DEFAULT_BRIGHTNESS    102
#define CONFIG_DEFAULT_SLEEP_TIMEOUT 300  /* seconds */

// Initialize config store — loads from NVS, or uses defaults.
// Must be called before display_init().
void config_store_init(void);

// Getters — return current in-memory values.
uint8_t  config_store_get_brightness(void);
uint32_t config_store_get_sleep_timeout_ms(void);  /* returns milliseconds */
bool     config_store_get_day_night(void);         /* sky + backlight follow the time of day; default off */

// Setters — update in-memory value AND persist to NVS.
void config_store_set_brightness(uint8_t duty);
void config_store_set_sleep_timeout(uint16_t seconds);
void config_store_set_day_night(bool enabled);

// Serialize full config to JSON. Returns number of bytes written (excluding null).
// Output is null-terminated.
uint16_t config_store_serialize_json(char *buf, uint16_t buf_sz);

#endif // CONFIG_STORE_H
