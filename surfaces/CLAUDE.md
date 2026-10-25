# surfaces/ — Agent B

The dashboard (review and BLOCKED screens, live trace with the call transcript, CT strip, audit packet), the
callback (Twilio + Cartesia), the shared desk on SpacetimeDB, and the forward-to-check door. Everything here
consumes Decision objects; nothing here scores an invoice. Run guide: `surfaces/README.md`.

- Every file in `contract/samples/` must render without errors; tests drive a real detector process over HTTP.
- Talk to the detector only through its HTTP API (`surfaces/client.py`) or `from detector import Doris`;
  never import detector internals, never edit `detector/`.
- Every string from a Decision or a caller is attacker-controlled: escape it in HTML, TwiML and email.
- Public routes live only on the hooks port (8789) behind secret tokens; the dashboard (8788) stays on localhost.
- The desk is a mirror: screens still ask the dashboard for the decision, and fall back to polling if it's down.
