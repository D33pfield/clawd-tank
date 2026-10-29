# Garmin watch transport

The daemon can mirror its state to a Garmin watch app (Connect IQ) through a small HTTPS relay. The watch app and relay live outside this repo; this document covers the daemon side.

```
daemon --HTTPS POST--> relay <--HTTPS GET-- watch (via the phone's Garmin Connect app)
```

A Connect IQ app can only pull data (its web requests go watch -> phone -> internet), so the daemon posts the latest snapshot and the watch polls it.

## Enabling it

Create `~/.clawd-tank/garmin.json` (mode 600) and restart the daemon:

```json
{"url": "https://example.com/clawd/state", "token": "<relay write token>", "enabled": true}
```

- `url` and `token` are required. `url` must be `https://` (plain `http://` is accepted only for localhost, for local relays and tests); anything else, including `file://`, is rejected with a warning. `enabled` defaults to true; set it to `false` to turn the transport off without deleting the file.
- The daemon warns if the file is readable by other users; keep it `chmod 600`, since it holds the relay write token.
- Only headless mode (CLI daemon) reads this file. The menu bar app does not create the transport.
- The daemon logs `Transport 'garmin' connected` on start. A missing or invalid file simply leaves the transport out.

## Behaviour (`host/clawd_tank_daemon/garmin_client.py`)

`GarminClient` implements the `TransportClient` interface but talks stateless HTTP, so it counts as connected as soon as it starts and delivery is best-effort.

- It folds the daemon's payloads into one snapshot: `set_status` sets the status, `add` / `dismiss` / `clear` maintain the card list. `sweeping` is a one-shot and does not replace the status. `set_time`, `set_sessions` and other actions are ignored. The transport stays on protocol v1, so the daemon sends it `set_status` payloads.
- Snapshot posted to the relay: `{"status": "working_1", "cards": [{"p": "<project>", "m": "<message>"}]}` with the newest 4 cards, newest first. Strings are cut to the relay's limits (status 16, project 24, message 60 characters) and sent as UTF-8, so a snapshot always stays under the relay's 2 KB cap; an oversized snapshot would otherwise be rejected on every post.
- Updates are debounced (0.3 s) so bursts of events produce one POST. A heartbeat re-posts every 60 s so the watch can tell "daemon quiet" from "daemon gone" (the relay reports the snapshot's age).
- On disconnect (daemon shutdown) it waits briefly for any POST already in flight, then posts `sleeping` with no cards, using a 3 s timeout so an unreachable relay can't stall shutdown for long.
- POST failures are logged and never affect the BLE or simulator transports.
- Redirects are never followed, and any 3xx counts as a failure: the bearer token is only ever sent to the configured URL. If you see a 301/302 in the log, fix the configured URL (usually `http://` instead of `https://`).
- Requests carry `User-Agent: clawd-tank-daemon`. Cloudflare rejects Python's default `Python-urllib` User-Agent with a 403.
- Standard library only, no new dependency.

## Tests

`host/tests/test_garmin_client.py` runs the client against local HTTP servers: snapshot folding, card cap and ordering, size limits with non-ASCII text, redirect refusal, URL validation, non-fatal failures, heartbeat, and config loading.

## Watch app and relay

The Connect IQ watch app, the relay server and its deployment, and the art pipeline live in the companion project `clawd-tank-garmin` (its README covers setup end to end).
