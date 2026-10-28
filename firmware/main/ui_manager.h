// firmware/main/ui_manager.h
#ifndef UI_MANAGER_H
#define UI_MANAGER_H

#include "lvgl.h"
#include "ble_service.h"

// Initialize UI manager. Must be called after lv_init() and display_init().
void ui_manager_init(void);

// Process a BLE event. Called from the UI task loop.
void ui_manager_handle_event(const ble_evt_t *evt);

// Run one iteration of LVGL timer handler. Called from the UI task loop.
void ui_manager_tick(void);

/* Re-apply the sky and backlight after the host changed brightness or the
 * day/night setting. Safe to call from any task; takes effect on the next tick. */
void ui_manager_config_changed(void);

/* Today's sunrise and sunset in local minutes since midnight, as computed by the
 * host (they shift through the year). Anything invalid, or never calling this,
 * means the default 06:30 / 18:30. Safe to call from any task. */
void ui_manager_set_sun_times(int sunrise_min, int sunset_min);

#ifdef SIMULATOR
#include "scene.h"

// Get current animation info (frame count and ms per frame).
void ui_manager_get_anim_info(int *frame_count, int *frame_ms);

// Get current animation frame index.
int ui_manager_get_frame_idx(void);

// Get the scene pointer (for query_state inspection from main thread).
scene_t *ui_manager_get_scene(void);
#endif

#endif // UI_MANAGER_H
