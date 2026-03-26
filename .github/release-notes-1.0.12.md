## Fixed

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
