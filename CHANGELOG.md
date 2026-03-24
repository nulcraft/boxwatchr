# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
