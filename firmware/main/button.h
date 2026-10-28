#pragma once
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

// Initialize the BOOT button to post BLE_EVT_BUTTON_PRESS on press.
void button_init(QueueHandle_t evt_queue);
