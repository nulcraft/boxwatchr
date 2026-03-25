## [1.0.6] - 2026-03-25

### Fixed
- Fixed IMAP fetch silently marking every processed message as read. Switched from RFC822 to BODY.PEEK[] so the server does not set the \Seen flag when boxwatchr reads a message. (#24)
