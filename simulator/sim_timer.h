/* Simulator timer implementation — drives esp_timer callbacks */
#pragma once
#include <stdint.h>

typedef void (*sim_timer_cb_t)(void *arg);

int  sim_timer_create(sim_timer_cb_t cb, void *arg);
int  sim_timer_start_periodic(int handle, uint64_t period_us);
int  sim_timer_stop(int handle);

/** Advance all active timers by elapsed_ms and fire due callbacks. */
void sim_timers_tick(uint32_t elapsed_ms);
