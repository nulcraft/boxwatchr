# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Renamed action dropdown options "Flag message" and "Remove flag" to "Mark as flagged" and "Mark as unflagged" for consistency with "Mark as read" / "Mark as unread".

## [1.0.15] - 2026-03-26

### Fixed
- Removed dead `delete` and `spam` action type branches from `notes.py`. (#50)
- Non-move actions (learn_ham, flag, etc.) no longer disappear and cause "Rule is invalid" when saving. The form parser now iterates action types directly and only pulls a destination for move actions, instead of zipping two parallel lists that came back different lengths due to disabled inputs not being submitted. (#51)

## [1.0.14] - 2026-03-26

### Fixed
- Removed `delete` and `spam` from the action type dropdown in the rule form. Both were removed from `valid_actions` in 1.0.12 but left in the template, causing "Rule is invalid" if either was selected. (#48)

## [1.0.13] - 2026-03-26

### Fixed
- Logout form was sending the CSRF token under the field name `csrf_token` instead of `_csrf_token`, causing every logout attempt to return 403. (#46)
- Logout `<button>` now has `background: none; border: none` in `.site-nav-logout` to strip browser default button chrome. (#46)

## [1.0.12] - 2026-03-26

### Fixed
- Update toast now shows on every page load; dismissal sets a `bw_skip_version` cookie for 30 days keyed to the version. Removed sessionStorage suppression and "Don't show again" button. (#43, #44)
- `_login_failures` dict in `login.py` now prunes stale entries for all IPs, not just the current one, preventing unbounded memory growth. (#44)
- `compute_content_hash` consolidated into `database.py`; duplicate definition removed from `main.py`. (#44)
- Dead `delete` and `spam` branches removed from `imap.execute_action`. (#44)
- `_check_imap` in `health.py` now passes `timeout=10` to `IMAPClient`, preventing indefinite hangs during health checks. (#44)
- `logout` changed from GET to POST with CSRF validation; nav logout link replaced with a form. (#44)
- `_test_imap_rate_limited` in `setup.py` now checks the attempt count before recording, so the limit is enforced at exactly `_TEST_IMAP_MAX_ATTEMPTS`. (#44)
- `_update_log_level` in `config.py` now updates `DatabaseHandler` instances in addition to `StreamHandler`s. (#44)
- `reload_rules` alias removed from `rules.py`; all callers updated to use `load_rules` directly. (#44)
- Dashboard rule match counts now computed in SQL using `JSON_EXTRACT` and `GROUP BY` instead of fetching all rows into Python. (#44)

## [1.0.11] - 2026-03-26

### Fixed
- Emails manually moved out of Junk back to the monitored folder are no longer re-processed. Dedup now uses a SHA-256 hash of sender, subject, date, and recipients rather than the Message-ID header, which is not present on all messages. (#41, #42)

## [1.0.10] - 2026-03-26

### Changed
- Redesigned update toast: header now shows "Update X.Y.Z Available", body renders GitHub release notes as markdown via marked.js with a scrollable 25vh container, changelog link removed, close button shrunk. (#38)
- Styled page headers with a white-to-gray gradient text, fading underline, and filled Bootstrap Icons in both headers and nav links. Removed back-link breadcrumbs from rule form and email detail pages. (#39)

## [1.0.9] - 2026-03-26

### Added
- Added DISCLOSURES.md covering privacy, credential storage, AI assistance, third-party software licenses, and security vulnerability reporting. (#32)

### Fixed
- Fixed version check reporting a stale version by switching from reading the raw VERSION file on the main branch to the GitHub Releases API, which always reflects the latest published release. (#33)

### Changed
- Renamed rule condition field labels for clarity: "local part (before @)" to "Username", "domain name" to "Subdomain + domain", "domain root" to "Domain (no subdomain)". Removed redundant Sender/Recipient prefix from dropdown options. (#36)
- Made the version number in the navbar more prominent and adjusted the logo gap.

### Removed
- Removed PyYAML dependency, which was no longer used. (#35)

## [1.0.8] - 2026-03-25

### Changed
- Disabled unused rspamd workers (fuzzy storage, proxy) and modules (dkim_signing, arc_signing, replies, spamtrap, neural, mx_check, ratelimit) to reduce memory and CPU overhead. (#30)
- Set rspamd log level to warning to reduce log noise. (#30)
- Reduced Redis RDB snapshot frequency from every 60 seconds to every 10 minutes. (#30)
- Capped Redis memory at 256MB with LRU eviction to prevent unbounded growth. (#30)

## [1.0.7] - 2026-03-25

### Changed
- Replaced the `>` prefix in the navbar brand with the logo image. (#28)
- Pushed navbar links to the right side within a constrained container so they align with page content. (#28)
- Added favicon and apple-touch-icon across all pages. (#28)
- Updated the login page to show the logo and version number. (#28)

## [1.0.6] - 2026-03-25

### Fixed
- Fixed IMAP fetch silently marking every processed message as read. Switched from RFC822 to BODY.PEEK[] so the server does not set the \Seen flag when boxwatchr reads a message. (#24)

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
