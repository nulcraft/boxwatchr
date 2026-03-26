## Fixed

- Emails manually moved out of Junk back to the monitored folder are no longer re-processed. Dedup now uses a SHA-256 hash of sender, subject, date, and recipients rather than the Message-ID header, which is not present on all messages. (#41, #42)
