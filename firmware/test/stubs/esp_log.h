// Stub for esp_log.h — used in host-side unit tests only
#ifndef ESP_LOG_H
#define ESP_LOG_H

#define ESP_LOGI(tag, fmt, ...) ((void)(tag))
#define ESP_LOGW(tag, fmt, ...) ((void)(tag))
#define ESP_LOGE(tag, fmt, ...) ((void)(tag))
#define ESP_LOGD(tag, fmt, ...) ((void)(tag))
#define ESP_LOGV(tag, fmt, ...) ((void)(tag))

#endif // ESP_LOG_H
