# boxwatchr

Self-hosted IMAP email filtering daemon. Single Docker container with supervisord. Monitors IMAP via IDLE/polling, scores mail with rspamd, evaluates rules, executes IMAP actions, logs to SQLite. Flask dashboard on port 80.

## Critical Fixes

- [ ] **rspamd error**: "Cannot receive neighbours data: Network error" on `local/neighbours` (benign or not?)

## Roadmap Changes & Implementations

- [ ] **Support multiple IMAP accounts**
    - [ ] Rules should be associated to IMAP account custom ID
    - [ ] "IMAP Account" drop-down on the following pages, with the first account created as the default selected/populated on page loads:
        - [ ] `/emails` (only show emails per account, not massive list)
        - [ ] `/logs` (additional filter for selecting a specific IMAP account)
        - [ ] `/rules` (IMPORT/EXPORT buttons to allow easy rule migration to other IMAP accounts)
        - [ ] `/config`
            - "IMAP Account" becomes "IMAP Accounts"
            - Drop-down to select different accounts with IMAP credentials form populated by Javascript
        - [ ] `/setup` (no need to allow creating multiple IMAP accounts at startup -- can be left alone unless backend database changes need to be made at this stage)
    - [ ] Rules page should be updated to allow selecting a specific account for rules, IMPORT/EXPORT buttons apply to the specific account chosen in the drop-down
    - [ ] One watch thread per account on backend
    - [ ] Emails page should feature drop-down for account selection as well
    - [ ] Deleting IMAP account deletes all associated rules and email history
        - `logs` table retains all records, but /logs page no longer includes ability to select previous IMAP account for filtering
- [ ] "**Continue After Match** / **Stop After Match**" flags
    - Add per-rule "Continue After Match" vs "Stop After Match" select. Default is to Stop After Match. This will allow chaining rules, such as having a "Learn Ham" rule that then falls through to the next rule. Rule order will be important.
- [ ] **Update GitHub Actions workflow to Node.js 24.** The docker-publish.yml workflow uses actions/checkout@v4, docker/build-push-action@v6, and docker/login-action@v3, all of which run on Node.js 20. GitHub is forcing Node.js 24 as the default starting June 2, 2026.
- [ ] **Investigate threading model for multiple IMAP accounts.** One watch thread per account is the goal. Evaluate whether the current thread-per-account approach is sufficient or whether a more structured concurrency model is needed before implementing multi-account support.

## Completed Tasks

- [x] **Investigate proper GIT handling** for Claude so I can focus more on issues and code changes as opposed to figuring out how Github actually works (for now...I really need to learn Git shit) (**2026-03-24**, no PR link for this)
- [x] **Periodic rescan** to catch emails missed by IMAP IDLE (**2026-03-24**, [#11](https://github.com/nulcraft/boxwatchr/pull/11))
    - Implemented as a 5-minute interval inside the IDLE loop. The IDLE session terminates early when the interval is due, `startup_scan()` runs against the DB, then IDLE resumes. Polling mode does not need this since it already catches missed messages on the next cycle.
- [x] **Reduced database.py connection boilerplate** with a `_db()` context manager (**2026-03-24**, [#13](https://github.com/nulcraft/boxwatchr/pull/13))
    - Replaced the repeated `get_connection() / try / finally conn.close()` pattern across all database functions and web modules. The context manager is exported as `db_connection` for use outside the module. `_flush()` retains direct connection handling due to its re-queue logic on failure.
- [x] **Version label and update check toast** (**2026-03-25**, [#15](https://github.com/nulcraft/boxwatchr/pull/15))
    - Shows the running version in the navbar. Checks GitHub for a newer version once per browser session and shows a toast bottom-right with a changelog link, session-dismiss, and per-version "Don't show again." Configurable via a Check for Updates toggle on the config page.
- [x] **Renamed rule condition field labels for clarity** (**2026-03-26**, [#36](https://github.com/nulcraft/boxwatchr/pull/36))
    - Removed em dashes and redundant Sender/Recipient prefix from dropdown options. Renamed "local part (before @)" to "Username", "domain name" to "Subdomain + domain", and "domain root" to "Domain (no subdomain)". Updated help text and README to match.