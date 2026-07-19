# Phone2PC Protocol v6

All control messages are UTF-8 JSON text frames. File payloads use binary WebSocket frames. The PC server enforces a 1 MiB inbound-frame limit; both peers also enforce a 256 KiB clipboard limit, a 64 KiB remote-input limit, and a 10 GiB file limit at the application layer.

## Authentication

1. PC sends `AUTH_REQUIRED` with `protocol` and a random `challenge`.
2. A new device sends `PAIR` with its `device_id` and the six-digit code shown by the PC.
3. PC returns `PAIR_SUCCESS` with a device-bound token and immediately rotates the pairing code.
4. A returning device sends `AUTH` with `HMAC-SHA256(token, challenge)` in `response`.
5. PC returns `AUTH_OK`. Clipboard, input, and file messages are rejected before this point.

Only one authenticated device is active. A new authenticated connection replaces the old connection.

## Clipboard and input

`CLIPBOARD_SYNC` contains `source` (`PC` or `PHONE`) and `content`. Plain authenticated text frames are treated as remote input. Unknown JSON control messages are ignored by protocol-aware clients.

## File transfer

1. Sender emits `FILE_OFFER` with `file_id`, basename, and byte size.
2. Receiver validates limits, creates a unique `.part` file, and replies `FILE_ACCEPT` or `FILE_ERROR`.
3. Sender emits 256 KiB binary frames. Android senders pause every 4 MiB until `FILE_ACK`; PC senders await every WebSocket send for transport backpressure.
4. Sender emits `FILE_END` with the exact size and SHA-256 digest.
5. Receiver verifies size and digest, atomically renames the `.part` file, then replies `FILE_COMPLETE`.

Disconnects, timeouts, overflow, and hash mismatches delete partial files and fail the active transfer. Batch sends are serialized to preserve binary-frame routing and bound memory use.
