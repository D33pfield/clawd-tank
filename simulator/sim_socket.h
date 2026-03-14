// simulator/sim_socket.h
#ifndef SIM_SOCKET_H
#define SIM_SOCKET_H

#include <stdbool.h>

#define SIM_SOCKET_DEFAULT_PORT 19872

// Window command types delivered via show_window / hide_window / set_window actions.
typedef enum {
    SIM_WIN_CMD_SHOW,
    SIM_WIN_CMD_HIDE,
    SIM_WIN_CMD_SET_PINNED,
} sim_win_cmd_type_t;

typedef struct {
    sim_win_cmd_type_t type;
    bool pinned;  /* only meaningful for SIM_WIN_CMD_SET_PINNED */
} sim_win_cmd_t;

// Start TCP listener on given port. Spawns a background thread.
// Returns 0 on success, -1 on error.
int sim_socket_init(int port);

// Drain any queued BLE events from the socket thread.
// Call from the main loop before ui_manager_tick().
// Returns true if any event was processed.
bool sim_socket_process(void);

// Drain any queued window commands from the socket thread.
// Calls handler for each command. Returns true if any command was processed.
bool sim_socket_process_window_cmds(void (*handler)(const sim_win_cmd_t *cmd));

// Send a JSON event line to the currently-connected client (if any).
// json_line must be a null-terminated JSON string (no trailing newline needed).
// Returns true if the line was sent successfully.
bool sim_socket_send_event(const char *json_line);

// Stop listener, close sockets, join thread.
void sim_socket_shutdown(void);

#endif
