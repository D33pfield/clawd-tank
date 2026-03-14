#include "sim_timer.h"
#include <stdbool.h>

#define MAX_TIMERS 4

typedef struct {
    sim_timer_cb_t callback;
    void    *arg;
    uint64_t period_us;
    uint64_t elapsed_us;
    bool     active;
} sim_timer_entry_t;

static sim_timer_entry_t s_timers[MAX_TIMERS];
static int s_timer_count = 0;

int sim_timer_create(sim_timer_cb_t cb, void *arg)
{
    if (s_timer_count >= MAX_TIMERS) return -1;
    int h = s_timer_count++;
    s_timers[h].callback = cb;
    s_timers[h].arg = arg;
    s_timers[h].active = false;
    return h;
}

int sim_timer_start_periodic(int handle, uint64_t period_us)
{
    if (handle < 0 || handle >= s_timer_count) return -1;
    s_timers[handle].period_us = period_us;
    s_timers[handle].elapsed_us = 0;
    s_timers[handle].active = true;
    return 0;
}

int sim_timer_stop(int handle)
{
    if (handle < 0 || handle >= s_timer_count) return -1;
    s_timers[handle].active = false;
    return 0;
}

void sim_timers_tick(uint32_t elapsed_ms)
{
    uint64_t elapsed_us = (uint64_t)elapsed_ms * 1000;
    for (int i = 0; i < s_timer_count; i++) {
        if (!s_timers[i].active) continue;
        s_timers[i].elapsed_us += elapsed_us;
        while (s_timers[i].active && s_timers[i].elapsed_us >= s_timers[i].period_us) {
            s_timers[i].elapsed_us -= s_timers[i].period_us;
            s_timers[i].callback(s_timers[i].arg);
        }
    }
}
