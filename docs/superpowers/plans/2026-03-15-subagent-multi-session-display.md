# Subagent & Multi-Session Display Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Display multiple Clawds for multiple sessions and a HUD counter for subagents on the 320×172 pixel display.

**Architecture:** Three-layer changes: (1) daemon computes per-session animation list + subagent count, (2) protocol carries this as `set_sessions` action with stable IDs, (3) firmware renders 1-4 Clawd sprites with independent animations + HUD overlay. Protocol versioning ensures backwards compatibility.

**Tech Stack:** C (ESP-IDF 5.3.2 + LVGL 9.5.0), Python (asyncio daemon), cJSON, NimBLE GATT

**Spec:** `docs/superpowers/specs/2026-03-15-subagent-display-design.md`
**Protocol Reference:** `docs/protocol-changelog.md`

---

## Chunk 1: Daemon — Session Ordering & Protocol v2

Python-only changes. Fully testable with existing pytest infrastructure. No firmware changes yet.

### Task 1: Session Order Tracking

**Files:**
- Modify: `host/clawd_tank_daemon/daemon.py`
- Modify: `host/clawd_tank_daemon/session_store.py`
- Test: `host/tests/test_session_state.py`

The daemon needs a `_session_order` list of `(session_id, display_id)` tuples and a `_next_display_id` counter. Sessions are appended on arrival, removed on end. The `display_id` is an incrementing integer, never reused.

- [ ] **Step 1: Write failing tests for session ordering**

Add to `host/tests/test_session_state.py`:

```python
def test_session_order_tracks_arrival():
    """Sessions should be tracked in arrival order with stable display IDs."""
    d = make_daemon()
    d._handle_message({"event": "session_start", "session_id": "aaa"})
    d._handle_message({"event": "session_start", "session_id": "bbb"})
    d._handle_message({"event": "session_start", "session_id": "ccc"})
    assert d._session_order == [("aaa", 1), ("bbb", 2), ("ccc", 3)]


def test_session_order_removes_on_end():
    """Ending a middle session shifts later ones down."""
    d = make_daemon()
    d._handle_message({"event": "session_start", "session_id": "aaa"})
    d._handle_message({"event": "session_start", "session_id": "bbb"})
    d._handle_message({"event": "session_start", "session_id": "ccc"})
    d._handle_message({"event": "dismiss", "session_id": "bbb", "hook": "SessionEnd"})
    assert d._session_order == [("aaa", 1), ("ccc", 3)]


def test_session_order_display_ids_never_reuse():
    """Display IDs increment and are never reused even after removal."""
    d = make_daemon()
    d._handle_message({"event": "session_start", "session_id": "aaa"})
    d._handle_message({"event": "dismiss", "session_id": "aaa", "hook": "SessionEnd"})
    d._handle_message({"event": "session_start", "session_id": "bbb"})
    assert d._session_order == [("bbb", 2)]


def test_session_order_created_on_tool_use_if_missing():
    """tool_use creates session in order if not already tracked."""
    d = make_daemon()
    d._handle_message({"event": "tool_use", "session_id": "aaa"})
    assert len(d._session_order) == 1
    assert d._session_order[0][0] == "aaa"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py -v -k "session_order"`
Expected: FAIL — `_session_order` attribute does not exist

- [ ] **Step 3: Implement session order tracking**

In `daemon.py`, add to `__init__`:
```python
self._session_order: list[tuple[str, int]] = []  # (session_id, display_id)
self._next_display_id: int = 1
```

In `_update_session_state`, when a session is first created (via `session_start`, `tool_use`, or `subagent_start`), add it to `_session_order`:
```python
if session_id not in [sid for sid, _ in self._session_order]:
    self._session_order.append((session_id, self._next_display_id))
    self._next_display_id += 1
```

On `SessionEnd` (dismiss), remove from `_session_order`:
```python
self._session_order = [(sid, did) for sid, did in self._session_order if sid != session_id]
```

Also remove from `_session_order` in `_evict_stale_sessions` when a session is evicted.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py -v -k "session_order"`
Expected: All 4 tests PASS

- [ ] **Step 5: Update session persistence for session_order**

`session_store.py` needs to save/load `_session_order` and `_next_display_id`. Add these to the serialized dict. Update `save_sessions()` and `load_sessions()`.

- [ ] **Step 6: Write tests for session order persistence**

Add to `host/tests/test_session_store.py`:
```python
def test_session_order_round_trip(tmp_path):
    path = tmp_path / "sessions.json"
    states = {"aaa": {"state": "working", "last_event": 100.0}}
    order = [("aaa", 1), ("bbb", 2)]
    save_sessions(path, states, order=order, next_id=3)
    loaded_states, loaded_order, loaded_next_id = load_sessions(path)
    assert loaded_order == [("aaa", 1), ("bbb", 2)]
    assert loaded_next_id == 3
```

- [ ] **Step 7: Run all session tests**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py tests/test_session_store.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add host/clawd_tank_daemon/daemon.py host/clawd_tank_daemon/session_store.py host/tests/test_session_state.py host/tests/test_session_store.py
git commit -m "feat(daemon): track session arrival order with stable display IDs"
```

---

### Task 2: Rewrite Display State Computation

**Files:**
- Modify: `host/clawd_tank_daemon/daemon.py`
- Test: `host/tests/test_session_state.py`

`_compute_display_state()` changes from returning a string like `"working_2"` to returning a dict with `anims`, `ids`, `subagents`, and optional `overflow`.

- [ ] **Step 1: Write failing tests for new display state format**

```python
def test_display_state_single_session_typing():
    d = make_daemon()
    d._handle_message({"event": "session_start", "session_id": "aaa"})
    d._handle_message({"event": "tool_use", "session_id": "aaa"})
    state = d._compute_display_state()
    assert state == {"anims": ["typing"], "ids": [1], "subagents": 0}


def test_display_state_working_with_subagents_becomes_building():
    d = make_daemon()
    d._handle_message({"event": "session_start", "session_id": "aaa"})
    d._handle_message({"event": "tool_use", "session_id": "aaa"})
    d._handle_message({"event": "subagent_start", "session_id": "aaa", "agent_id": "sub1"})
    state = d._compute_display_state()
    assert state["anims"] == ["building"]
    assert state["subagents"] == 1


def test_display_state_preserves_arrival_order():
    d = make_daemon()
    d._handle_message({"event": "session_start", "session_id": "aaa"})
    d._handle_message({"event": "tool_use", "session_id": "aaa"})
    d._handle_message({"event": "session_start", "session_id": "bbb"})
    # bbb is registered → idle
    state = d._compute_display_state()
    assert state["anims"] == ["typing", "idle"]
    assert state["ids"] == [1, 2]


def test_display_state_overflow_with_5_sessions():
    d = make_daemon()
    for i in range(5):
        sid = f"s{i}"
        d._handle_message({"event": "session_start", "session_id": sid})
        d._handle_message({"event": "tool_use", "session_id": sid})
    state = d._compute_display_state()
    assert len(state["anims"]) == 4  # max visible
    assert state["overflow"] == 1


def test_display_state_sleeping_when_no_sessions():
    d = make_daemon()
    state = d._compute_display_state()
    assert state == {"status": "sleeping"}


def test_display_state_middle_session_removed():
    d = make_daemon()
    d._handle_message({"event": "session_start", "session_id": "aaa"})
    d._handle_message({"event": "tool_use", "session_id": "aaa"})
    d._handle_message({"event": "session_start", "session_id": "bbb"})
    d._handle_message({"event": "tool_use", "session_id": "bbb"})
    d._handle_message({"event": "session_start", "session_id": "ccc"})
    d._handle_message({"event": "tool_use", "session_id": "ccc"})
    # Remove middle
    d._handle_message({"event": "dismiss", "session_id": "bbb", "hook": "SessionEnd"})
    state = d._compute_display_state()
    assert state["anims"] == ["typing", "typing"]
    assert state["ids"] == [1, 3]  # id 2 gone, others preserved
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py -v -k "display_state"`
Expected: FAIL — `_compute_display_state` returns a string, not a dict

- [ ] **Step 3: Rewrite `_compute_display_state()`**

Replace the existing method with the version from the spec (lines 282-320 of the spec). Key changes:
- Returns `{"status": "sleeping"}` when no sessions
- Iterates `self._session_order[:4]` to build `anims` and `ids` lists
- Maps `working` + subagents → `"building"`, `working` alone → `"typing"`
- Counts total subagents across all sessions
- Adds `overflow` when sessions > 4

- [ ] **Step 4: Update `_broadcast_display_state_if_changed()`**

Change `self._last_display_state` from `str` to `dict` (initialize as `{"status": "sleeping"}`). The comparison `new_state != self._last_display_state` works the same with dicts.

The broadcast payload generation needs to produce the v2 `set_sessions` JSON:
```python
if "status" in new_state:
    # Whole-device state (sleeping)
    payload = json.dumps({"action": "set_status", "status": new_state["status"]})
else:
    payload = json.dumps({"action": "set_sessions", **new_state})
```

Note: For now, always send v2 format. Protocol version gating (v1 fallback) will be added in Task 4.

- [ ] **Step 5: Fix existing tests that depend on old string format**

Some existing tests in `test_session_state.py` may check `_compute_display_state()` return value as a string. Update them to expect the new dict format. For example, `test_no_sessions_sleeping` should assert `== {"status": "sleeping"}` instead of `== "sleeping"`.

- [ ] **Step 6: Run all tests**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py -v`
Expected: All PASS (both old updated tests and new tests)

- [ ] **Step 7: Commit**

```bash
git add host/clawd_tank_daemon/daemon.py host/tests/test_session_state.py
git commit -m "feat(daemon): rewrite display state to per-session anims + subagent count"
```

---

### Task 3: Protocol v2 Payload Generation

**Files:**
- Modify: `host/clawd_tank_daemon/protocol.py`
- Test: `host/tests/test_protocol.py`

The daemon needs to generate `set_sessions` JSON payloads and still support `set_status` for v1 transports.

- [ ] **Step 1: Write failing tests for v2 payload**

Add to `host/tests/test_protocol.py`:
```python
def test_display_state_to_payload_v2_sessions():
    state = {"anims": ["typing", "thinking"], "ids": [1, 2], "subagents": 3}
    payload = display_state_to_ble_payload(state)
    parsed = json.loads(payload)
    assert parsed["action"] == "set_sessions"
    assert parsed["anims"] == ["typing", "thinking"]
    assert parsed["ids"] == [1, 2]
    assert parsed["subagents"] == 3
    assert "overflow" not in parsed


def test_display_state_to_payload_v2_with_overflow():
    state = {"anims": ["typing"] * 4, "ids": [1, 2, 3, 4], "subagents": 0, "overflow": 2}
    payload = display_state_to_ble_payload(state)
    parsed = json.loads(payload)
    assert parsed["overflow"] == 2


def test_display_state_to_payload_sleeping():
    state = {"status": "sleeping"}
    payload = display_state_to_ble_payload(state)
    parsed = json.loads(payload)
    assert parsed == {"action": "set_status", "status": "sleeping"}


def test_display_state_to_payload_v1_fallback():
    """For v1 transports, convert dict state to legacy set_status string."""
    state = {"anims": ["typing", "thinking"], "ids": [1, 2], "subagents": 3}
    payload = display_state_to_v1_payload(state)
    parsed = json.loads(payload)
    assert parsed["action"] == "set_status"
    assert parsed["status"] == "working_2"  # 2 sessions working
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd host && .venv/bin/pytest tests/test_protocol.py -v -k "display_state_to"`
Expected: FAIL — functions don't exist

- [ ] **Step 3: Implement payload generation functions**

Add to `protocol.py`:

```python
def display_state_to_ble_payload(state: dict) -> str:
    """Convert display state dict to v2 JSON payload."""
    if "status" in state:
        return json.dumps({"action": "set_status", "status": state["status"]})
    payload = {"action": "set_sessions", **state}
    return json.dumps(payload)


def display_state_to_v1_payload(state: dict) -> str:
    """Convert display state dict to legacy v1 set_status payload."""
    if "status" in state:
        return json.dumps({"action": "set_status", "status": state["status"]})
    working = sum(1 for a in state.get("anims", []) if a in ("typing", "building"))
    if working > 0:
        status = f"working_{min(working, 3)}"
    elif "thinking" in state.get("anims", []):
        status = "thinking"
    elif "confused" in state.get("anims", []):
        status = "confused"
    else:
        status = "idle"
    return json.dumps({"action": "set_status", "status": status})
```

- [ ] **Step 4: Run tests**

Run: `cd host && .venv/bin/pytest tests/test_protocol.py -v`
Expected: All PASS

- [ ] **Step 5: Update `_broadcast_display_state_if_changed()` to use new functions**

Replace inline payload creation with `display_state_to_ble_payload(new_state)`.

- [ ] **Step 6: Commit**

```bash
git add host/clawd_tank_daemon/protocol.py host/clawd_tank_daemon/daemon.py host/tests/test_protocol.py
git commit -m "feat(daemon): v2 set_sessions payload generation with v1 fallback"
```

---

### Task 4: Per-Transport Protocol Version

**Files:**
- Modify: `host/clawd_tank_daemon/daemon.py`
- Modify: `host/clawd_tank_daemon/ble_client.py`
- Test: `host/tests/test_session_state.py`

The daemon needs to track protocol version per transport and send the appropriate payload format.

- [ ] **Step 1: Add `_transport_versions` dict to daemon**

```python
self._transport_versions: dict[str, int] = {}  # transport_name → protocol version
```

- [ ] **Step 2: Set simulator version to latest on connect**

In `_on_transport_connect()`, if transport is simulator, set version to latest:
```python
self._transport_versions[name] = 2  # simulator always latest
```

- [ ] **Step 3: Add BLE version reading**

In `ble_client.py`, add a method to read the version characteristic after connecting. If the characteristic doesn't exist (v1 firmware), return 1. The UUID will be defined as a constant (generate a UUID during implementation).

- [ ] **Step 4: Update `_broadcast_display_state_if_changed()` to use per-transport version**

Instead of broadcasting the same payload to all transports, generate per-transport payloads based on version:
```python
for name, queue in self._transport_queues.items():
    version = self._transport_versions.get(name, 1)
    if version >= 2:
        payload = display_state_to_ble_payload(new_state)
    else:
        payload = display_state_to_v1_payload(new_state)
    await queue.put(payload)
```

- [ ] **Step 5: Write tests for version-based payload selection**

```python
def test_v1_transport_gets_set_status():
    """V1 transport should receive legacy set_status format."""
    d = make_daemon()
    d._transport_versions["ble"] = 1
    # ... verify set_status payload sent to ble queue

def test_v2_transport_gets_set_sessions():
    """V2 transport should receive set_sessions format."""
    d = make_daemon()
    d._transport_versions["sim"] = 2
    # ... verify set_sessions payload sent to sim queue
```

- [ ] **Step 6: Run all daemon tests**

Run: `cd host && .venv/bin/pytest tests/ -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add host/clawd_tank_daemon/daemon.py host/clawd_tank_daemon/ble_client.py host/tests/test_session_state.py
git commit -m "feat(daemon): per-transport protocol versioning with v1/v2 payload selection"
```

---

## Chunk 2: Firmware & Simulator — Protocol Parsing

C changes to parse the new `set_sessions` action and expose the protocol version characteristic.

### Task 5: Extend `ble_evt_t` for Multi-Session Data

**Files:**
- Modify: `firmware/main/ble_service.h`

- [ ] **Step 1: Add `MAX_VISIBLE_SESSIONS` and new event type**

```c
#define MAX_VISIBLE_SESSIONS 4

// In ble_evt_type_t enum, add:
BLE_EVT_SET_SESSIONS,
```

- [ ] **Step 2: Add sessions struct to `ble_evt_t`**

The struct already has `uint8_t status` for `BLE_EVT_SET_STATUS`. Add a `sessions` struct alongside it. Since the existing struct is flat (not a union), we need to add the new fields carefully. Add after the `status` field:

```c
/* set_sessions data (BLE_EVT_SET_SESSIONS) */
uint8_t session_anim_count;
uint8_t session_anims[MAX_VISIBLE_SESSIONS];
uint16_t session_ids[MAX_VISIBLE_SESSIONS];
uint8_t subagent_count;
uint8_t session_overflow;
```

- [ ] **Step 3: Commit**

```bash
git add firmware/main/ble_service.h
git commit -m "feat(ble): extend ble_evt_t with set_sessions fields"
```

---

### Task 6: Parse `set_sessions` Action in Firmware

**Files:**
- Modify: `firmware/main/ble_service.c`

- [ ] **Step 1: Add `parse_anim_name()` helper**

Maps animation name strings to `clawd_anim_id_t` values. Place near `parse_display_status()`:

```c
static int parse_anim_name(const char *str) {
    if (strcmp(str, "idle") == 0)     return CLAWD_ANIM_IDLE;
    if (strcmp(str, "typing") == 0)   return CLAWD_ANIM_TYPING;
    if (strcmp(str, "thinking") == 0) return CLAWD_ANIM_THINKING;
    if (strcmp(str, "building") == 0) return CLAWD_ANIM_BUILDING;
    if (strcmp(str, "confused") == 0) return CLAWD_ANIM_CONFUSED;
    return -1;
}
```

- [ ] **Step 2: Add `set_sessions` parsing to `parse_notification_json()`**

After the existing `set_status` handler, add:

```c
} else if (strcmp(action->valuestring, "set_sessions") == 0) {
    cJSON *anims = cJSON_GetObjectItem(json, "anims");
    cJSON *ids = cJSON_GetObjectItem(json, "ids");
    cJSON *subagents = cJSON_GetObjectItem(json, "subagents");
    if (!anims || !cJSON_IsArray(anims) || !ids || !cJSON_IsArray(ids)) {
        cJSON_Delete(json);
        return;
    }
    evt.type = BLE_EVT_SET_SESSIONS;
    evt.session_anim_count = 0;
    evt.subagent_count = subagents && cJSON_IsNumber(subagents) ? (uint8_t)subagents->valueint : 0;
    cJSON *overflow = cJSON_GetObjectItem(json, "overflow");
    evt.session_overflow = overflow && cJSON_IsNumber(overflow) ? (uint8_t)overflow->valueint : 0;

    int anim_size = cJSON_GetArraySize(anims);
    int id_size = cJSON_GetArraySize(ids);
    int count = anim_size < id_size ? anim_size : id_size;
    if (count > MAX_VISIBLE_SESSIONS) count = MAX_VISIBLE_SESSIONS;

    for (int i = 0; i < count; i++) {
        cJSON *a = cJSON_GetArrayItem(anims, i);
        cJSON *id = cJSON_GetArrayItem(ids, i);
        if (!a || !cJSON_IsString(a) || !id || !cJSON_IsNumber(id)) continue;
        int anim = parse_anim_name(a->valuestring);
        if (anim < 0) continue;
        evt.session_anims[evt.session_anim_count] = (uint8_t)anim;
        evt.session_ids[evt.session_anim_count] = (uint16_t)id->valueint;
        evt.session_anim_count++;
    }
}
```

- [ ] **Step 3: Build firmware to verify compilation**

Run: `cd firmware && idf.py build`
Expected: Build succeeds (new code is compiled but not yet called by UI manager)

- [ ] **Step 4: Commit**

```bash
git add firmware/main/ble_service.c
git commit -m "feat(ble): parse set_sessions action with anims, ids, subagents"
```

---

### Task 7: Parse `set_sessions` in Simulator

**Files:**
- Modify: `simulator/sim_ble_parse.c`

- [ ] **Step 1: Add same `parse_anim_name()` function**

Mirror the firmware implementation (simulator compiles its own parser).

- [ ] **Step 2: Add `set_sessions` handler to `sim_ble_parse_json()`**

Same parsing logic as the firmware. Return code 0 (BLE event) for `set_sessions`, same as other BLE events.

- [ ] **Step 3: Build simulator**

Run: `cd simulator && cmake -B build && cmake --build build`
Expected: Build succeeds

- [ ] **Step 4: Commit**

```bash
git add simulator/sim_ble_parse.c
git commit -m "feat(sim): parse set_sessions action (mirrors firmware parser)"
```

---

### Task 8: Protocol Version GATT Characteristic

**Files:**
- Modify: `firmware/main/ble_service.c`

- [ ] **Step 1: Generate and add version characteristic UUID**

```c
// Version Characteristic: generate a new UUID
static const ble_uuid128_t version_chr_uuid = BLE_UUID128_INIT(
    /* generate via uuidgen and convert to little-endian bytes */
);
```

- [ ] **Step 2: Add read-only access callback**

```c
static int version_access_cb(uint16_t conn_handle, uint16_t attr_handle,
                              struct ble_gatt_access_ctxt *ctxt, void *arg) {
    if (ctxt->op == BLE_GATT_ACCESS_OP_READ_CHR) {
        const char *ver = "2";
        int rc = os_mbuf_append(ctxt->om, ver, strlen(ver));
        return rc == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
    }
    return BLE_ATT_ERR_UNLIKELY;
}
```

- [ ] **Step 3: Register in GATT service definition**

Add to the `gatt_svcs` array, after the config characteristic:

```c
{
    .uuid = &version_chr_uuid.u,
    .access_cb = version_access_cb,
    .flags = BLE_GATT_CHR_F_READ,
},
```

- [ ] **Step 4: Build firmware**

Run: `cd firmware && idf.py build`
Expected: Build succeeds

- [ ] **Step 5: Commit**

```bash
git add firmware/main/ble_service.c
git commit -m "feat(ble): add protocol version GATT characteristic (v2)"
```

---

## Chunk 3: Firmware — Multi-Slot Scene Rendering

The core visual change: rendering 1-4 independent Clawd sprites.

### Task 9: `clawd_slot_t` and Scene Struct Refactor

**Files:**
- Modify: `firmware/main/scene.c`
- Modify: `firmware/main/scene.h`

- [ ] **Step 1: Define `clawd_slot_t` struct in `scene.c`**

```c
typedef struct {
    lv_obj_t *sprite_img;
    lv_image_dsc_t frame_dsc;
    uint8_t *frame_buf;
    int frame_buf_size;
    clawd_anim_id_t cur_anim;
    clawd_anim_id_t fallback_anim;
    int frame_idx;
    uint32_t last_frame_tick;
    uint16_t display_id;   /* stable ID from daemon, for diffing */
    bool active;
} clawd_slot_t;
```

- [ ] **Step 2: Replace single-sprite fields in `scene_t` with slot array**

Replace `sprite_img`, `frame_dsc`, `frame_buf`, `frame_buf_size`, `cur_anim`, `frame_idx`, `last_frame_tick`, `fallback_anim` with:

```c
clawd_slot_t slots[MAX_VISIBLE_SESSIONS];
int active_slot_count;
```

- [ ] **Step 3: Refactor `ensure_frame_buf` to work per-slot**

```c
static void ensure_frame_buf(clawd_slot_t *slot, int w, int h) {
    int needed = w * h * 4;
    if (slot->frame_buf && slot->frame_buf_size >= needed) return;
    free(slot->frame_buf);
    slot->frame_buf = heap_caps_malloc(needed, MALLOC_CAP_SPIRAM);
    slot->frame_buf_size = slot->frame_buf ? needed : 0;
}
```

Note: use `heap_caps_malloc` with `MALLOC_CAP_SPIRAM` to allocate from PSRAM.

- [ ] **Step 4: Refactor `decode_and_apply_frame` to work per-slot**

```c
static void decode_and_apply_frame(clawd_slot_t *slot) {
    const anim_def_t *def = &anim_defs[slot->cur_anim];
    int idx = slot->frame_idx;
    int w = def->width, h = def->height;
    ensure_frame_buf(slot, w, h);
    if (!slot->frame_buf) return;
    const uint16_t *frame_rle = &def->rle_data[def->frame_offsets[idx]];
    rle_decode_argb8888(frame_rle, slot->frame_buf, w * h, TRANSPARENT_KEY);
    slot->frame_dsc.header.w = w;
    slot->frame_dsc.header.h = h;
    slot->frame_dsc.header.cf = LV_COLOR_FORMAT_ARGB8888;
    slot->frame_dsc.header.stride = w * 4;
    slot->frame_dsc.data = slot->frame_buf;
    slot->frame_dsc.data_size = w * h * 4;
    lv_image_set_src(slot->sprite_img, &slot->frame_dsc);
}
```

- [ ] **Step 5: Refactor `scene_create` to initialize slot 0**

Instead of creating a single `sprite_img`, create `slots[0]` as the initial active slot. All other slots start with `active = false` and `sprite_img = NULL`.

```c
for (int i = 0; i < MAX_VISIBLE_SESSIONS; i++) {
    s->slots[i].active = false;
    s->slots[i].sprite_img = NULL;
    s->slots[i].frame_buf = NULL;
    s->slots[i].frame_buf_size = 0;
    s->slots[i].display_id = 0;
}
// Activate slot 0 with default animation
scene_activate_slot(s, 0, CLAWD_ANIM_IDLE);
```

- [ ] **Step 6: Add slot activation/deactivation helpers**

```c
static void scene_activate_slot(scene_t *s, int idx, clawd_anim_id_t anim) {
    clawd_slot_t *slot = &s->slots[idx];
    if (!slot->sprite_img) {
        slot->sprite_img = lv_image_create(s->container);
        lv_image_set_inner_align(slot->sprite_img, LV_IMAGE_ALIGN_CENTER);
    }
    slot->active = true;
    slot->cur_anim = anim;
    slot->fallback_anim = anim;
    slot->frame_idx = 0;
    slot->last_frame_tick = lv_tick_get();
    decode_and_apply_frame(slot);
    const anim_def_t *def = &anim_defs[anim];
    lv_obj_align(slot->sprite_img, LV_ALIGN_BOTTOM_MID, 0, def->y_offset);
    lv_obj_clear_flag(slot->sprite_img, LV_OBJ_FLAG_HIDDEN);
}

static void scene_deactivate_slot(scene_t *s, int idx) {
    clawd_slot_t *slot = &s->slots[idx];
    slot->active = false;
    if (slot->sprite_img) {
        lv_obj_add_flag(slot->sprite_img, LV_OBJ_FLAG_HIDDEN);
    }
    free(slot->frame_buf);
    slot->frame_buf = NULL;
    slot->frame_buf_size = 0;
}
```

- [ ] **Step 7: Refactor `scene_tick` to iterate active slots**

Replace the single-sprite frame advancement with a loop over `slots[0..MAX_VISIBLE_SESSIONS-1]`, only processing slots where `active == true`.

- [ ] **Step 8: Update public API functions**

Refactor `scene_set_clawd_anim` and `scene_set_fallback_anim` to work with slot 0 by default (backwards compatible). Add new functions for multi-slot operations:

```c
/* scene.h */
void scene_set_sessions(scene_t *scene, const uint8_t *anims, const uint16_t *ids,
                        int count, uint8_t subagent_count, uint8_t overflow);
```

- [ ] **Step 9: Implement `scene_set_sessions` with X positioning**

```c
static const int x_centers[][4] = {
    {160},              /* 1 session */
    {107, 213},         /* 2 sessions */
    {80, 160, 240},     /* 3 sessions */
    {64, 128, 192, 256} /* 4 sessions */
};

void scene_set_sessions(scene_t *s, const uint8_t *anims, const uint16_t *ids,
                        int count, uint8_t subagent_count, uint8_t overflow) {
    if (count < 1) count = 1;
    if (count > MAX_VISIBLE_SESSIONS) count = MAX_VISIBLE_SESSIONS;

    /* Diff old IDs vs new IDs to determine add/remove (Task 12) */
    /* For now: simple set — deactivate all, reactivate with new data */

    for (int i = 0; i < MAX_VISIBLE_SESSIONS; i++) {
        if (i < count) {
            scene_activate_slot(s, i, (clawd_anim_id_t)anims[i]);
            s->slots[i].display_id = ids[i];
            /* Position: center at x_centers[count-1][i] */
            int cx = x_centers[count - 1][i];
            const anim_def_t *def = &anim_defs[anims[i]];
            lv_obj_set_pos(s->slots[i].sprite_img, cx - def->width / 2,
                           172 - def->height + def->y_offset);
        } else {
            scene_deactivate_slot(s, i);
        }
    }
    s->active_slot_count = count;

    /* HUD + badge updates (Task 11) */
}
```

- [ ] **Step 10: Build firmware**

Run: `cd firmware && idf.py build`
Expected: Build succeeds

- [ ] **Step 11: Build simulator**

Run: `cd simulator && cmake -B build && cmake --build build`
Expected: Build succeeds

- [ ] **Step 12: Commit**

```bash
git add firmware/main/scene.c firmware/main/scene.h
git commit -m "feat(scene): multi-slot rendering with per-session positioning"
```

---

### Task 10: UI Manager — Handle `BLE_EVT_SET_SESSIONS`

**Files:**
- Modify: `firmware/main/ui_manager.c`

- [ ] **Step 1: Add `BLE_EVT_SET_SESSIONS` case to `ui_manager_handle_event`**

```c
case BLE_EVT_SET_SESSIONS: {
    /* Wake from sleep if needed */
    if (s_display_status == DISPLAY_STATUS_SLEEPING) {
        display_set_brightness(config_store_get_brightness());
    }
    s_display_status = DISPLAY_STATUS_IDLE; /* no longer sleeping/disconnected */

    scene_set_sessions(s_scene,
        evt->session_anims, evt->session_ids,
        evt->session_anim_count, evt->subagent_count, evt->session_overflow);
    break;
}
```

- [ ] **Step 2: Update `sweeping` oneshot to apply to all active slots**

The existing `BLE_EVT_SET_STATUS` handler for `DISPLAY_STATUS_SWEEPING` currently sets the animation on the single sprite. Refactor to iterate all active slots when sweeping:

```c
case BLE_EVT_SET_STATUS: {
    /* ... existing logic ... */
    if (new_status == DISPLAY_STATUS_SWEEPING) {
        /* Apply sweeping to all active slots */
        for (int i = 0; i < MAX_VISIBLE_SESSIONS; i++) {
            /* scene function to trigger sweeping on all slots */
        }
    }
    break;
}
```

- [ ] **Step 3: Build and test with simulator**

Run: `cd simulator && cmake -B build && cmake --build build`
Then test manually:
```bash
./simulator/build/clawd-tank-sim --headless --listen &
# In another terminal, send set_sessions:
echo '{"action":"set_sessions","anims":["typing","thinking"],"ids":[1,2],"subagents":0}' | nc localhost 19872
```

- [ ] **Step 4: Commit**

```bash
git add firmware/main/ui_manager.c
git commit -m "feat(ui): handle BLE_EVT_SET_SESSIONS with multi-slot scene update"
```

---

## Chunk 4: Firmware — HUD Overlay & Badges

### Task 11: Pixel-Art Bitmap Font

**Files:**
- Create: `firmware/main/pixel_font.h`
- Create: `firmware/main/pixel_font.c`

- [ ] **Step 1: Create bitmap font header**

```c
/* pixel_font.h — 5×5 pixel-art bitmap digits */
#pragma once
#include "lvgl.h"

/* Draw a pixel-art string at (x, y) with given pixel size and color.
 * Supported characters: 0-9, x (multiply), + */
void pixel_font_draw(lv_obj_t *canvas, const char *text,
                     int x, int y, int px_size, lv_color_t color);
```

- [ ] **Step 2: Implement bitmap font**

Create `pixel_font.c` with the 5×5 bitmask lookup table (same digit definitions as in the layout editor JS) and a `pixel_font_draw` function that iterates characters and draws filled rectangles on an LVGL canvas.

- [ ] **Step 3: Build**

Run: `cd firmware && idf.py build`
Expected: Build succeeds

- [ ] **Step 4: Commit**

```bash
git add firmware/main/pixel_font.h firmware/main/pixel_font.c
git commit -m "feat(ui): pixel-art bitmap font for HUD counter and badges"
```

---

### Task 12: HUD Counter & Badge Rendering

**Files:**
- Modify: `firmware/main/scene.c`
- Modify: `firmware/main/scene.h`

- [ ] **Step 1: Add HUD LVGL objects to `scene_t`**

```c
/* HUD overlay */
lv_obj_t *hud_container;
lv_obj_t *hud_canvas;       /* for pixel font rendering */
lv_obj_t *hud_crab_img;     /* mini-crab sprite icon */
uint8_t hud_subagent_count;
uint8_t hud_overflow;
int hud_total_sessions;      /* for narrow mode badge format */
```

- [ ] **Step 2: Create HUD container in `scene_create`**

```c
s->hud_container = lv_obj_create(s->container);
lv_obj_remove_style_all(s->hud_container);
lv_obj_set_size(s->hud_container, lv_pct(100), lv_pct(100));
lv_obj_set_style_bg_opa(s->hud_container, LV_OPA_TRANSP, 0);
lv_obj_add_flag(s->hud_container, LV_OBJ_FLAG_HIDDEN); /* shown when subagents > 0 */
```

- [ ] **Step 3: Implement `scene_update_hud` function**

Called from `scene_set_sessions`. Updates the pixel-art counter and badge visibility:
- If `subagent_count > 0`: show mini-crab icon + "×N" at top-left
- If `overflow > 0`: show "+N" badge at top-right (full screen) or "×total" (narrow)
- If both are 0: hide HUD container

- [ ] **Step 4: Wire `scene_set_sessions` to call `scene_update_hud`**

- [ ] **Step 5: Build and test visually with simulator**

Test with simulator headless + screenshot:
```bash
./simulator/build/clawd-tank-sim --headless \
  --events 'connect; wait 200; raw {"action":"set_sessions","anims":["typing"],"ids":[1],"subagents":3}; wait 2000' \
  --screenshot-dir ./shots/ --screenshot-on-event
```

- [ ] **Step 6: Commit**

```bash
git add firmware/main/scene.c firmware/main/scene.h
git commit -m "feat(scene): HUD subagent counter and session overflow badge"
```

---

### Task 13: Clock Centering

**Files:**
- Modify: `firmware/main/scene.c`

- [ ] **Step 1: Move time label to horizontal center**

In `scene_create`, change the time label alignment from its current position to `LV_ALIGN_TOP_MID`:

```c
lv_obj_align(s->time_label, LV_ALIGN_TOP_MID, 0, 4);
```

- [ ] **Step 2: Build and verify**

- [ ] **Step 3: Commit**

```bash
git add firmware/main/scene.c
git commit -m "feat(scene): center clock horizontally to avoid HUD badge conflicts"
```

---

## Chunk 5: Firmware — Transition Animations

### Task 14: Session Diff Logic

**Files:**
- Modify: `firmware/main/scene.c`

- [ ] **Step 1: Implement ID diffing in `scene_set_sessions`**

Replace the simple "deactivate all, reactivate" approach with proper diffing:

```c
void scene_set_sessions(scene_t *s, ...) {
    /* Build lookup of old IDs → slot index */
    uint16_t old_ids[MAX_VISIBLE_SESSIONS];
    int old_count = s->active_slot_count;
    for (int i = 0; i < old_count; i++)
        old_ids[i] = s->slots[i].display_id;

    /* Determine which IDs are new (added), which are gone (removed),
     * which moved position */
    for (int new_i = 0; new_i < count; new_i++) {
        int old_i = find_id_in(old_ids, old_count, ids[new_i]);
        if (old_i >= 0) {
            /* Existing session — update animation, reposition */
            s->slots[new_i] = s->slots[old_i]; /* move slot data */
            /* ... update position ... */
        } else {
            /* New session — activate, start off-screen right */
            scene_activate_slot(s, new_i, anims[new_i]);
            s->slots[new_i].display_id = ids[new_i];
            /* Start at x > 320 for walk-in animation */
        }
    }
    /* Deactivate slots for removed IDs */
    for (int old_i = 0; old_i < old_count; old_i++) {
        if (find_id_in(ids, count, old_ids[old_i]) < 0) {
            /* Fade out this slot */
        }
    }
}
```

- [ ] **Step 2: Build and test**

- [ ] **Step 3: Commit**

```bash
git add firmware/main/scene.c
git commit -m "feat(scene): diff session IDs for add/remove detection"
```

---

### Task 15: Walk-In and Fade-Out Animations

**Files:**
- Modify: `firmware/main/scene.c`

Note: This task depends on the crab-walking sprite asset. If not available, use fallback sliding animation.

- [ ] **Step 1: Add `CLAWD_ANIM_WALKING` to enum**

Add to `scene.h`:
```c
CLAWD_ANIM_WALKING,  /* crab-walking sideways */
```

And the corresponding `anim_defs` entry (placeholder until sprite is created):
```c
[CLAWD_ANIM_WALKING] = {
    /* Use idle sprite as placeholder until walking sprite is created */
    .rle_data = idle_rle_data,
    .frame_offsets = idle_frame_offsets,
    .frame_count = IDLE_FRAME_COUNT,
    .frame_ms = IDLE_FRAME_MS,
    .looping = true,
    .width = IDLE_WIDTH,
    .height = IDLE_HEIGHT,
    .y_offset = 8,
},
```

- [ ] **Step 2: Implement slide animation for repositioning**

When a slot needs to move to a new X position, use LVGL `lv_anim_t`:

```c
static void slide_slot_to(clawd_slot_t *slot, int target_x, int duration_ms) {
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, slot->sprite_img);
    lv_anim_set_values(&a, lv_obj_get_x(slot->sprite_img), target_x);
    lv_anim_set_duration(&a, duration_ms);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
    lv_anim_set_exec_cb(&a, (lv_anim_exec_xcb_t)lv_obj_set_x);
    lv_anim_start(&a);
}
```

- [ ] **Step 3: Implement fade-out for departing slots**

```c
static void fade_out_slot(scene_t *s, int idx, int duration_ms) {
    clawd_slot_t *slot = &s->slots[idx];
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, slot->sprite_img);
    lv_anim_set_values(&a, LV_OPA_COVER, LV_OPA_TRANSP);
    lv_anim_set_duration(&a, duration_ms);
    lv_anim_set_exec_cb(&a, (lv_anim_exec_xcb_t)lv_obj_set_style_opa);
    lv_anim_set_completed_cb(&a, fade_out_complete_cb);
    lv_anim_start(&a);
}
```

- [ ] **Step 4: Wire into `scene_set_sessions` diff logic**

- New ID → start off-screen (x=350), slide to target over 600ms
- Removed ID → fade out over 400ms
- Existing ID that moved → slide to new position over 600ms

- [ ] **Step 5: Add walking animation switch during transitions**

When a slot is sliding, temporarily switch its animation to `CLAWD_ANIM_WALKING`. Set a completion callback that restores the session-state animation. Handle horizontal flip based on movement direction.

- [ ] **Step 6: Build and test with simulator**

- [ ] **Step 7: Commit**

```bash
git add firmware/main/scene.c firmware/main/scene.h
git commit -m "feat(scene): walk-in and fade-out transition animations"
```

---

## Chunk 6: Sprite Assets & Integration Testing

### Task 16: Mini-Crab Sprite Asset

**Files:**
- Input: `assets/svg-animations/mini-crab-typing.svg` (exists as placeholder)
- Create: `firmware/main/assets/sprite_mini_crab.h` (via pipeline)

- [ ] **Step 1: Run sprite pipeline on mini-crab SVG**

```bash
python tools/svg2frames.py assets/svg-animations/mini-crab-typing.svg /tmp/mini-crab-frames/ --fps 8 --duration auto --scale 4
python tools/png2rgb565.py /tmp/mini-crab-frames/ firmware/main/assets/sprite_mini_crab.h --name mini_crab
```

If the SVG is too simplistic, create a better placeholder or have the user provide the final animation.

- [ ] **Step 2: Include in HUD rendering code**

Add `#include "assets/sprite_mini_crab.h"` to `scene.c` and use the sprite for the HUD icon.

- [ ] **Step 3: Commit**

```bash
git add firmware/main/assets/sprite_mini_crab.h firmware/main/scene.c
git commit -m "feat(assets): mini-crab sprite for HUD subagent counter"
```

---

### Task 17: End-to-End Integration Testing

**Files:**
- No new files — testing with simulator + daemon

- [ ] **Step 1: Test single session with subagents**

```bash
./simulator/build/clawd-tank-sim --headless --listen &
# Send typing session with 3 subagents
echo '{"action":"set_sessions","anims":["typing"],"ids":[1],"subagents":3}' | nc localhost 19872
# Verify: 1 Clawd centered, HUD shows ×3
```

- [ ] **Step 2: Test multi-session**

```bash
# Send 2 sessions
echo '{"action":"set_sessions","anims":["typing","thinking"],"ids":[1,2],"subagents":1}' | nc localhost 19872
# Verify: 2 Clawds at x=107 and x=213
```

- [ ] **Step 3: Test session removal (diff)**

```bash
# Remove session 1, keep session 2
echo '{"action":"set_sessions","anims":["thinking"],"ids":[2],"subagents":0}' | nc localhost 19872
# Verify: session 1 fades out, session 2 slides to center
```

- [ ] **Step 4: Test overflow badge**

```bash
echo '{"action":"set_sessions","anims":["typing","typing","typing","typing"],"ids":[1,2,3,4],"subagents":0,"overflow":2}' | nc localhost 19872
# Verify: 4 Clawds + "+2" badge top-right
```

- [ ] **Step 5: Test backwards compatibility (v1 set_status still works)**

```bash
echo '{"action":"set_status","status":"working_1"}' | nc localhost 19872
# Verify: single Clawd with typing animation (legacy behavior)
```

- [ ] **Step 6: Run Python test suite**

```bash
cd host && .venv/bin/pytest -v
```
Expected: All PASS

- [ ] **Step 7: Run C unit tests**

```bash
cd firmware/test && make clean && make test
```
Expected: All PASS

- [ ] **Step 8: Final commit**

```bash
git commit -m "test: end-to-end verification of multi-session + subagent display"
```

---

## Summary

| Chunk | Description | Testability |
|-------|------------|-------------|
| 1 | Daemon: session ordering, display state dict, v2 payloads, protocol versioning | Python pytest |
| 2 | Firmware/sim: `set_sessions` parsing, version GATT characteristic | `idf.py build`, simulator build |
| 3 | Firmware: multi-slot scene rendering, UI manager integration | Simulator visual testing |
| 4 | Firmware: HUD overlay (pixel font, subagent counter, badges), clock | Simulator visual testing |
| 5 | Firmware: transition animations (walk-in, fade-out, reposition) | Simulator visual testing |
| 6 | Sprite assets, end-to-end integration testing | Full stack testing |

**Assets that need to be created separately** (by the user, not by this plan):
- Crab-walking sprite (~180×180) — Task 15 uses idle as placeholder
- Final mini-crab sprite — Task 16 uses placeholder SVG through pipeline

---

## Implementation Notes (from plan review)

**CRITICAL — the implementing agent MUST address these:**

1. **`ble_evt_t` struct (Task 5):** The current struct is flat, not a union. Converting to a union (as the spec requires) means ALL existing code that accesses `evt.id`, `evt.project`, `evt.message`, `evt.status` must be updated to use the correct union member. Alternatively, keep the struct flat and just add the new session fields — it's 16 extra bytes per event on a 243-byte struct, and the FreeRTOS queue depth is small. Pick whichever is simpler.

2. **Async test helpers (Task 1-2):** `_handle_message()` is `async`. All tests calling it must use `@pytest.mark.asyncio` and `await`. Check the existing test patterns in `test_session_state.py` for the correct approach.

3. **Update ALL callers of `_compute_display_state()` (Task 2):** After rewriting it to return a dict, these call sites break and must be updated:
   - `_broadcast_display_state_if_changed()` — uses the return value to build JSON payload
   - `_replay_active_for()` (line 328) — does `json.dumps({"action": "set_status", "status": state})` which breaks when `state` is a dict
   - The compact (`PreCompact`) handler (line 155) — same issue with the fallback payload
   - Use the new `display_state_to_ble_payload()` function from Task 3 everywhere

4. **`save_sessions` / `load_sessions` signature (Task 1):** Update ALL call sites in `daemon.py` when changing the function signatures (`_persist_sessions()`, `__init__` loading).

5. **`_broadcast_display_state_if_changed` writes directly to transports** (not through queues). Match the existing pattern — iterate `self._transports` and call `transport.write_notification()` directly on connected transports. Do NOT switch to queue-based broadcasting unless intentionally refactoring.

6. **`lv_obj_set_style_opa` needs a wrapper (Task 15):** LVGL 9 `lv_obj_set_style_opa()` takes 3 params `(obj, opa, selector)`, but `lv_anim_exec_xcb_t` expects `(void*, int32_t)`. Write a wrapper:
   ```c
   static void set_sprite_opa(void *obj, int32_t v) {
       lv_obj_set_style_opa(obj, (lv_opa_t)v, 0);
   }
   ```

7. **Use `malloc()` not `heap_caps_malloc()` for frame buffers (Task 9):** The simulator compiles the same `scene.c` and doesn't have `heap_caps_malloc`. The existing code uses plain `malloc()`. Keep it that way.

8. **Set `frame_dsc.header.magic = LV_IMAGE_HEADER_MAGIC` (Task 9):** When initializing `clawd_slot_t.frame_dsc`, set the magic number. Without it, LVGL 9 won't recognize the image descriptor.

9. **Narrow screen handling (missing from plan):** Add a step in Task 10 or as a new Task between 10 and 11: when `scene_set_width` transitions to narrow (107px), deactivate all slots except slot 0. When transitioning back to full (320px), reactivate slots and reposition. The `scene_set_sessions` function needs to check current scene width and only position the appropriate number of slots. In narrow mode, show session badge ("×N" total) instead of overflow badge ("+N" extra).

10. **Slot data move overlap (Task 14):** When diffing and reassigning slots, copy the old slot array to a temp before reassigning to avoid data corruption when indices overlap.

11. **Add `pixel_font.c` to simulator's `CMakeLists.txt`:** The simulator explicitly lists source files. New `.c` files must be added there.

12. **Update `docs/protocol-changelog.md`:** Add a step in Task 8 to update the changelog with the version GATT characteristic UUID once generated.
