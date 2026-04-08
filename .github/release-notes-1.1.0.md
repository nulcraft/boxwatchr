## Added

- `email_age_days` and `email_age_hours` rule condition fields for time-based filtering.
- Time-deferred processing: emails with no current match but a future time-based match are held and re-evaluated automatically.
- `matches_regex` condition operator (case-insensitive) for text fields.
- `enabled` flag on rules: disabled rules are skipped during evaluation and can be toggled from the rules list.
- Duplicate rule button on the rules list.
- Rule hit count and last triggered date shown on the rules list.
- Rule name search on the rules list (client-side, no page reload).
- Rule templates on the new rule form for quick-start rule creation.
- Rule form pre-fills sender and subject when navigated to from an email detail page.
- `condition_groups` support for grouped AND/OR condition sets within a rule.
- Discord webhook notification action type for rules and a global fallback webhook config key.
- `add_label` action type to apply IMAP keyword flags to matched messages.
- rspamd symbol breakdown stored per email and displayed on the email detail page.
- Email body text preview (first 2000 chars of the text/plain part) on the email detail page.
- Manual action buttons on the email detail page: move, mark read/unread, flag, unflag, learn spam/ham, add label.
- Folder, search, and rule-match filters on the emails list.
- Rule name on the emails list links to the rule editor.
- Rule name on the email detail page links to the rule editor and shows "(deleted)" when the rule no longer exists.
- "Create rule from sender" shortcut link on the email detail page.
- `/api/rules/simulate` endpoint: dry-runs a rule definition against stored emails and returns matches.
- `EMAIL_RETENTION_DAYS` config key for automatic email pruning (0 disables it).
- `DISCORD_WEBHOOK_URL` global config key for a fallback Discord webhook.

## Changed

- IMAP authentication and folder errors during startup are now non-fatal: boxwatchr logs a warning and retries automatically instead of shutting down.
- rspamd scoring now stores per-symbol detail alongside the aggregate score.
- Rule matched JSON stores `rule_id` so rule names resolve correctly after a rename.
- Logo updated to transparent background variant.

## Fixed

- IMAP health check no longer triggers a fatal shutdown on transient connection drops that falsely report "folder does not exist". (#58)

## Attribution

Core ideas and initial implementation by [sNyteXx](https://github.com/sNyteXx). (#59)
