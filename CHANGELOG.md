# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.5] - 2026-03-25

### Added
- Added version number to the navbar next to the brand. Reads the VERSION file baked into the image at startup.
- Added update check: the dashboard fetches the latest version from GitHub once per browser session and shows a toast in the bottom right if a newer version is available. Includes a changelog link, a session-dismiss X button, and a per-version "Don't show again" option stored in localStorage.
- Added Check for Updates toggle to the config page. When disabled, no outbound request to GitHub is made and the toast is never shown.

## [1.0.4] - 2026-03-24

### Changed
- Replaced the repeated `get_connection() / try / finally conn.close()` pattern across `database.py` and all web modules with a `_db()` context manager, exported as `db_connection` for use outside the module.

## [1.0.3] - 2026-03-24

### Added
- Added nginx and Apache reverse proxy example configurations under `reverse-proxy/`. Covers SSL, OCSP stapling, security headers, IP-based access control, optional basic auth, and sub-path proxying for the rspamd web UI at `/rspamd/`.
- Added periodic rescan every 5 minutes in IMAP IDLE mode to catch messages missed when IDLE fires partway through a bulk move. The IDLE session terminates early, the folder is reconciled against the database, then IDLE resumes.

### Changed
- Documented that containers assigned a dedicated IP address (common in Unraid bridge/macvlan networks) must be accessed directly on the container ports (80 and 11334), not the host port mappings.

## [1.0.2] - 2026-03-24

### Fixed
- Marked `config/.env` as optional in `docker-compose.yml` so the container starts without the file present.

### Changed
- Added setup instructions for Unraid, Portainer, Synology, and other Docker GUI platforms that configure containers through environment variables rather than an env file.
- Clarified that timestamps are stored in UTC and converted to the configured timezone at display time, so `TZ` can be changed at any time without affecting stored data.
- Fixed incorrect claim that the rspamd web interface is inaccessible when no password is set. The generated password is printed to the container logs at startup.
- Removed stale reference to a `greylist.conf` file in the `config/` mount. Greylisting is disabled automatically by the container entrypoint and no file is written to the host.
- Moved Config page documentation into the dashboard pages section alongside Dashboard, Emails, Rules, and Logs.
- Expanded Config page description to cover all editable fields and behavior on save.
- Added reverse proxy recommendation to the security section, including a note that SSO/identity-aware proxy authentication passthrough has not been tested.

## [1.0.1] - 2026-03-24

### Fixed
- Baked greylist config into the container entrypoint so a fresh install works without needing any pre-created config files.

## [1.0.0] - 2026-03-24

### Added

- Initial Public Release.
- IMAP IDLE monitoring with polling fallback for detecting new messages in real time.
- rspamd integration for spam scoring with per-message symbol and score reporting.
- Bayesian learning support via rspamd for marking messages as ham or spam.
- Rule engine with ordered, first-match evaluation; conditions support sender, recipient, subject, headers, attachments, and rspamd score.
- IMAP actions: move (different folders, trash, Junk, etc.), copy, mark read/unread, flag/unflag.
- SQLite-backed email log with async write queue and automatic pruning.
- Flask web dashboard with email list, per-message detail view, rule manager, logs, and config pages.
- First-run setup wizard for IMAP account and application configuration.
- Single Docker container with supervisord managing rspamd, Redis, unbound, and Flask.
- Redis-backed rspamd Bayesian persistence via bind mount.
- Local recursive DNS via unbound for rspamd blocklist lookups.
