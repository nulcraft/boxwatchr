## Fixed

- Added downward database migration from v3 to v2 so containers previously upgraded to 1.1.0 can roll back to 1.0.16 without a fatal shutdown. (#63)
- Switched base Docker image from `debian:trixie-slim` to `debian:bookworm-slim` to resolve TLS/SSL connection failures when connecting to IMAP servers.
