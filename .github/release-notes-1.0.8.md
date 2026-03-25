## Changed

- Disabled unused rspamd workers (fuzzy storage, proxy) and modules (dkim_signing, arc_signing, replies, spamtrap, neural, mx_check, ratelimit) to reduce memory and CPU overhead. (#30)
- Set rspamd log level to warning to reduce log noise. (#30)
- Reduced Redis RDB snapshot frequency from every 60 seconds to every 10 minutes. (#30)
- Capped Redis memory at 256MB with LRU eviction to prevent unbounded growth. (#30)
