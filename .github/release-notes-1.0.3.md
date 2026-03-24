## Added

- Added nginx and Apache reverse proxy example configurations under `reverse-proxy/`. Covers SSL, OCSP stapling, security headers, IP-based access control, optional basic auth, and sub-path proxying for the rspamd web UI at `/rspamd/`.
- Added periodic rescan every 5 minutes in IMAP IDLE mode to catch messages missed when IDLE fires partway through a bulk move. The IDLE session terminates early, the folder is reconciled against the database, then IDLE resumes.

## Changed

- Documented that containers assigned a dedicated IP address (common in Unraid bridge/macvlan networks) must be accessed directly on the container ports (80 and 11334), not the host port mappings.
