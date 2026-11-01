# Phone (Android bridge) transport

The daemon can mirror its v2 session state to the Android bridge app (`clawd-tank-android`), which acts as the BLE central for the board. Same shape as the [Garmin transport](garmin-transport.md): the daemon POSTs a snapshot to an HTTPS relay and the phone polls it.

```
daemon --HTTPS POST--> relay <--HTTPS GET-- Android app --BLE--> board
```

## Enabling it

Create `~/.clawd-tank/phone.json` (mode 600) and restart the daemon:

```json
{"url": "https://example.com/clawd/phone", "token": "<relay write token>", "enabled": true}
```

The relay's `/clawd/phone` accepts bodies up to 4096 bytes (nginx allows 4k). Rules are the same as `garmin.json` (https only, http for localhost, no redirects, 600 permissions). Use a separate URL from the Garmin one. Only headless mode reads this file.

## Behaviour (`host/clawd_tank_daemon/phone_client.py`)

`PhoneClient` subclasses `GarminClient` and reuses its debounce (0.3 s), heartbeat (60 s), shutdown post and failure handling. It reports protocol v2 (`read_version` returns 2), so the daemon sends it `set_sessions` rather than `set_status`.

## Snapshot

```json
{
  "v": 2,
  "status": null,
  "anims": ["typing", "idle"],
  "ids": [1, 2],
  "subagents": 3,
  "overflow": 2,
  "cards": [{"id": "<session id>", "p": "<project>", "m": "<message>", "e": 1}]
}
```

- `status` is `null` while sessions are shown, or `"sleeping"` (and `anims`/`ids` empty) when the board should sleep. A `set_sessions` payload clears it.
- `anims`, `ids`, `subagents` and `overflow` are the `set_sessions` fields from `protocol-changelog.md`, forwarded as is. `overflow` is omitted when 0. At most 4 sessions.
- `cards` are the newest 4 notifications, newest first. `id` is the `add`/`dismiss` id, `e` is 1 for error alerts (`alert: "error"`). Strings use the Garmin limits; the worst case (control characters, which JSON escapes to 6 bytes each) is about 3.3 KB, under the phone endpoint's 4 KB body cap.
- `names` (optional) is each session's project, aligned with `anims`, 24 chars; present only when one is known.
- `music` (optional, see below) is what is playing on the Windows host.
- On daemon shutdown it posts `{"v": 2, "status": "sleeping", ...}` with no sessions or cards.

## Now playing (WSL on Windows, opt-in)

With `~/.clawd-tank/music.json` set to `{"enabled": true}` (optional `"interval"`: poll seconds, 1-30, default 2; no secrets in this file) and the phone transport enabled, the daemon starts one long-running `powershell.exe` (`host/clawd_tank_daemon/now_playing.ps1`, Windows PowerShell 5.1) that reads the Windows System Media Transport Controls, so any player that shows in the Windows media flyout counts (Spotify, browsers, foobar2000, ...). The script polls in process and prints a JSON line only when the state changes, plus the same line every 60 s; a session that is playing wins over the "current" one. `now_playing.py` turns those lines into:

```json
"music": {"playing": true, "artist": "Artist", "title": "Title", "track": 123456789}
```

- `playing` is true only while playing; paused, stopped and nothing playing are all false. With nothing playing `artist` and `title` are `""`.
- `artist` and `title`: control characters removed, whitespace collapsed, trimmed, at most 60 characters. `artist` falls back to the album artist.
- `track` (0 to 2^31-1) goes up by one when a new non-empty artist/title appears (pause, resume or a gap with nothing playing keep it). It starts at a random value, so a daemon restart does not repeat the previous id.
- `music` is absent when the feature is off, `powershell.exe` is not reachable, or the reader died or went quiet for 3 min (it is restarted after 30 s, doubling to 10 min). It never goes to the board, and it is dropped from a snapshot that would otherwise exceed the 4 KB cap.
- A change posts through the usual 0.3 s debounce; an unchanged state does not post. The reader exits when the daemon closes its stdin or dies, so there is no orphan `powershell.exe`. About 1% of one core and ~90 MB for the PowerShell process.

## Phone side

The app maps a snapshot to BLE writes: `set_time` on connect, `set_sessions` (or `set_status` when `status` is set), and `add`/`dismiss` by diffing `cards` against the previous poll.

## Relay

The relay needs a second endpoint that stores and serves this snapshot (write with the bearer token, read by the app, snapshot age reported like the Garmin one). The relay lives in `clawd-tank-garmin`.
