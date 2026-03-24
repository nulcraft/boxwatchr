# rspamd local.d overrides

Place `.conf` files here to customize rspamd. They are copied into rspamd's `local.d` on container startup.

Do not create `classifier-bayes.conf`, `redis.conf`, `options.inc`, or `worker-controller.inc` here. These are auto-generated at startup and will overwrite anything you put here.

`greylist.conf` is also written automatically at startup to disable rspamd greylisting. You can override it by placing your own `greylist.conf` here, but do so with caution.
