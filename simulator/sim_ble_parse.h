// simulator/sim_ble_parse.h
#ifndef SIM_BLE_PARSE_H
#define SIM_BLE_PARSE_H

#include "ble_service.h"
#include "cJSON.h"
#include <stdint.h>

// Parse a BLE-format JSON payload into a ble_evt_t.
// Returns 0 on success (event written to *out, caller should enqueue).
// Returns 1 for set_time (handled inline, no event to enqueue).
// Returns 2 for config actions (read_config/write_config — caller handles directly).
// Returns 3 for window commands (show_window/hide_window/set_window — caller handles directly).
// Returns 4 for query_state (caller sets pending flag for main thread).
// Returns -1 on parse error.
int sim_ble_parse_json(const char *buf, uint16_t len, ble_evt_t *out);

// Apply the keys of a config JSON object (brightness, sleep_timeout, day_night)
// to the config store, like the firmware's BLE config write. Missing keys are
// left unchanged. `tag` prefixes the log lines ("sim", "tcp").
void sim_apply_config(const cJSON *json, const char *tag);

#endif
