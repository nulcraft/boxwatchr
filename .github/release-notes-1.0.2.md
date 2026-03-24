## Fixed

- Marked `config/.env` as optional in `docker-compose.yml` so the container starts without the file present.

## Changed

- Added setup instructions for Unraid, Portainer, Synology, and other Docker GUI platforms that configure containers through environment variables rather than an env file.
- Clarified that timestamps are stored in UTC and converted to the configured timezone at display time, so `TZ` can be changed at any time without affecting stored data.
- Fixed incorrect claim that the rspamd web interface is inaccessible when no password is set. The generated password is printed to the container logs at startup.
- Removed stale reference to a `greylist.conf` file in the `config/` mount. Greylisting is disabled automatically by the container entrypoint and no file is written to the host.
- Moved Config page documentation into the dashboard pages section alongside Dashboard, Emails, Rules, and Logs.
- Expanded Config page description to cover all editable fields and behavior on save.
- Added reverse proxy recommendation to the security section, including a note that SSO/identity-aware proxy authentication passthrough has not been tested.
