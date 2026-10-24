# surfaces/ — Agent B

Review screen + CT timeline, callback (Cartesia voice over Twilio), forward-to-check
door, BLOCKED screen. Everything here consumes Decision objects; nothing here scores an invoice.

- Build mock-first against `contract/samples/*.json`; every sample must render without errors.
- Get live Decisions from the detector API (`python -m detector serve`, see `contract/README.md`)
  or from `data/outbox/`. Post callback results to `POST /decisions/{id}/callback_result`.
- Never import `detector` internals; use `from detector import Doris` or the HTTP API.
- Never edit `detector/`.
