## Fixed

- Training page now logs job start, message count, and completion summary to the application log. (#86)
- Training results table is now capped at 25 visible rows, preventing extreme page size when training large folders. (#86)
- IMAP action failures in `process_email` no longer abort remaining actions or lose the email record. Each action is now wrapped individually; if any fail, the email is stored with `processed=0` for retry on next startup, and the notes reflect which actions succeeded and which failed.
- Training session jobs are now removed from memory when the SSE stream closes, and cleaned up on the next training start if older than 5 minutes and completed, preventing unbounded memory growth in long-running deployments.
- `_test_imap_rate_limited` in `setup.py` now writes back the filtered attempt list before checking the rate limit, so stale timestamps are pruned for the current IP even when it is blocked.
- Version check cache no longer fires multiple concurrent HTTP requests on a cache miss. A `_cache_fetching` flag prevents concurrent fetches; concurrent callers return the stale cached value or `None` while a fetch is in progress.
- `rule_run` in `rule_form.py` now calls `imap.select_folder()` instead of `client.select_folder()` directly, ensuring consistent error handling and logging across all IMAP callers.
- Database schema creation log message now correctly reads "v2 created" instead of "v1 created".
- `action_sentence` in `notes.py` now returns a descriptive fallback string for unknown action types instead of silently returning an empty string.
- Removed `get_email_by_message_id` from `database.py`; the function was never called anywhere in the codebase.
- rspamd no longer logs a warning about missing `stats.ucl` on every container restart. (#83)

## Changed

- Dashboard Trained: Spam and Trained: Ham stats now show live Bayes classifier revision counts from the rspamd `/stat` endpoint instead of counting rows in the emails table. (#81)

## Security

- `SESSION_COOKIE_SECURE` is now configurable via the `SECURE_COOKIES=true` environment variable. Defaults to `false` to preserve compatibility with HTTP-only deployments; set to `true` when running behind an HTTPS reverse proxy.
- `/api/test-imap` now rejects connections to loopback addresses to prevent using the endpoint as an SSRF gadget against services running on the container itself.
