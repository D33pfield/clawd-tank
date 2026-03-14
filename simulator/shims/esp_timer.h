/* Simulator shim — esp_timer backed by sim_timer */
#pragma once
#include <stdint.h>
#include "sim_timer.h"

typedef int esp_err_t;

/* Handle is an int index stored in a pointer-sized slot */
typedef struct { int idx; } *esp_timer_handle_t;

typedef struct {
    void (*callback)(void *arg);
    const char *name;
} esp_timer_create_args_t;

#ifndef ESP_OK
#define ESP_OK 0
#endif

/* Allocate a small struct to hold the timer index */
#include <stdlib.h>

static inline esp_err_t esp_timer_create(const esp_timer_create_args_t *a, esp_timer_handle_t *h)
{
    int idx = sim_timer_create(a->callback, NULL);
    if (idx < 0) return -1;
    *h = (esp_timer_handle_t)malloc(sizeof(**h));
    (*h)->idx = idx;
    return ESP_OK;
}

static inline esp_err_t esp_timer_start_periodic(esp_timer_handle_t h, uint64_t period_us)
{
    return sim_timer_start_periodic(h->idx, period_us);
}

static inline esp_err_t esp_timer_stop(esp_timer_handle_t h)
{
    return sim_timer_stop(h->idx);
}
