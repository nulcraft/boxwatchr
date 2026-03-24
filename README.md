# boxwatchr

A self-hosted email filtering daemon that watches your IMAP mailbox, scores every incoming message with a spam engine, runs it through your custom rules, and takes action automatically. No cloud, no subscriptions, just your server doing exactly what you tell it to.

---

## What does it actually do?

boxwatchr connects to your email account over IMAP, watches your inbox in real-time, and for every new message it:

1. Scores the email with **rspamd** (a spam analysis engine) and gets back a numeric spam score.
2. Runs the email through your **custom rules** in order. If a rule matches, it wins.
3. **Takes action.** Move it to a folder, mark it read, flag it, submit it to spam training, whatever you told it to do.
4. Logs everything to a **SQLite database** and shows you a dashboard of everything that happened.

Think of it like email filters on steroids, with a spam engine backing up every decision, and a dashboard so you can see exactly what happened to every message.

---

## Features

- Real-time inbox monitoring via IMAP IDLE (or polling fallback)
- Spam scoring with rspamd, a production-grade spam analysis engine
- Bayesian spam learning that gets smarter every time you mark something as spam or ham
- Flexible rule engine with tons of conditions: sender, subject, domain, attachment type, spam score, and more
- Dashboard with stats, spam score histograms, and rule match counts
- Full email log so you can see every message that came through and what happened to it
- Dry run mode so you can see what boxwatchr would do before you commit to letting it run for real
- Rule changes made in the dashboard take effect immediately, no restart needed.
- Completely self-hosted. Nothing leaves your server.

---

## What you need

- **Docker** and **Docker Compose** installed on your server
- An email account that supports IMAP (Gmail, Fastmail, your own mail server, pretty much anything)
- A few minutes to set it up

That's it. You don't need to install Python, Redis, rspamd, or anything else. The Docker image includes everything.

---

## Getting started

### Step 1: Create your folder structure

On your server, create a folder for boxwatchr and set up the directory structure it expects:

```
mkdir -p boxwatchr/config boxwatchr/data
cd boxwatchr
```

### Step 2: Create your environment file (optional)

The `.env` file is optional. If you skip it, the container starts with sensible defaults (PUID/PGID 1000, UTC timezone, random rspamd password). If you want to set specific values, create `config/.env` and copy the following into it:

```
PUID=99
PGID=100
TZ=UTC
RSPAMD_PASSWORD=
```

**PUID and PGID** are the user ID and group ID that the boxwatchr process will run as inside the container. You want these to match your host user so that files written to your `config/` and `data/` folders are owned by you and not by root. To find your user's IDs, run `id` on your server:

```
$ id
uid=1000(youruser) gid=1000(youruser) groups=...
```

Set `PUID=1000` and `PGID=1000` (or whatever your actual IDs are).

**TZ** is your timezone in standard format, like `America/New_York`, `Europe/London`, or `Australia/Sydney`. All timestamps are stored in UTC and converted to your timezone for display, so you can change this at any time without affecting your data.

**RSPAMD_PASSWORD** is for the rspamd web interface on port 11334. If you leave this blank, boxwatchr generates a random password at every startup. If you want to actually log into the rspamd interface, set a password here so it stays the same between restarts. This field is completely optional and doesn't need to be in your .env file at all.

### Step 3: Create your docker-compose.yml

In your `boxwatchr` folder (not inside `config` or `data`, one level up), create `docker-compose.yml`:

```yaml
services:
  boxwatchr:
    image: ghcr.io/nulcraft/boxwatchr:latest
    container_name: boxwatchr
    restart: on-failure
    ports:
      - "8143:80"
      - "11334:11334"
    volumes:
      - ./config:/app/config
      - ./data:/app/data
    env_file:
      - path: ./config/.env
        required: false
```

By default the web dashboard runs on port **8143**. Port **11334** is the rspamd web interface. It's optional to expose it, but useful if you want to see what rspamd is doing.

Your folder structure should now look like this:

```
boxwatchr/
├── docker-compose.yml
├── config/
│   ├── .env
│   └── rspamd/
│       └── local.d/    (optional rspamd config overrides)
└── data/          (this will fill up automatically)
```

### Step 4: Start it up

```
docker compose up -d
```

The first startup takes a moment because rspamd and the DNS resolver need to initialize. Give it 15-30 seconds. Then open your browser and go to:

```
http://your-server-ip:8143
```

You'll be taken directly to the setup wizard.

---

## Running on Unraid, Portainer, Synology, or other Docker GUI platforms

If your platform manages containers through a GUI rather than docker-compose, skip the `.env` file entirely. Configure the container like this:

**Image:** `ghcr.io/nulcraft/boxwatchr:latest`

**Volumes:**
| Container path | Purpose |
|---|---|
| `/app/config` | Configuration storage |
| `/app/data` | Database and Bayesian data |

**Environment variables** (set these in your platform's GUI):
| Variable | Default | Description |
|---|---|---|
| `PUID` | `1000` | User ID to run as |
| `PGID` | `1000` | Group ID to run as |
| `TZ` | `UTC` | Display timezone |
| `RSPAMD_PASSWORD` | *(random)* | rspamd web interface password |

**Ports:**
| Host port | Container port | Purpose |
|---|---|---|
| `8143` | `80` | boxwatchr web dashboard |
| `11334` | `11334` | rspamd web interface (optional) |

Do not use an env file or `--env-file` flag. Pass variables directly through your platform's environment variable interface. Pointing `--env-file` at a file that does not exist will prevent the container from starting.

---

## First-time setup

The setup wizard walks you through everything the first time. You only do this once.

### IMAP credentials

- **Account Name:** Just a label for yourself, like "Gmail" or "Work Email" (I like to use my email address here)
- **IMAP Host:** Your mail server's IMAP address (e.g. `imap.gmail.com`, `imap.fastmail.com`)
- **Port:** Almost always `993` for SSL, `143` for STARTTLS or plain
- **TLS Mode:** Choose `SSL` (the default, most secure), `STARTTLS`, or `None` (not recommended unless you know what you're doing)
- **Username:** Your email address or IMAP username
- **Password:** Your email password, or an app password if your provider requires it

Once you fill in the credentials, click **Test Credentials**. This connects to your mail server and verifies everything works. If it connects successfully, a dropdown will appear letting you choose which folder to watch.

### Watch Folder

This is the folder boxwatchr monitors for new messages. Usually this is `INBOX`. If your server uses a different folder naming convention (some IMAP servers use `INBOX` under a namespace), the test connection will show you exactly what's available.

### Application settings

**Log Level** controls how much detail shows up in the system logs. `INFO` is the right choice for most people. `DEBUG` is very noisy. Only use it if something is broken and you're trying to figure out why.

**Log Retention** is how many days of log entries to keep. Logs are stored in the SQLite database on your server. Set `0` to keep everything forever, or enter a number like `7` to automatically clean up entries older than 1 week. Word of warning: the logs table can grow to be HUGE. You've been warned. Prune often if necessary.

**Dry Run:** Leave this alone for now. This is one of the most important features to understand, and it's covered in detail below. The short version is that when dry run is on, boxwatchr runs your rules and tells you what it would do, but doesn't actually move or modify any emails. Use this to make sure your rules are right before letting it loose.

### Web Password

Optional, but recommended if your dashboard is accessible over a network. Leave it blank if you're behind a reverse proxy that handles authentication, or if you're the only one who can reach it.

Click **Save** to complete setup. You will need to restart your container with `docker compose restart` and once it's back up and running, boxwatchr will start monitoring your mailbox.

---

## Rules

Rules are the heart of boxwatchr. Each rule has a **name**, a **match mode** (match all conditions, or match any condition), one or more **conditions**, and one or more **actions**.

Rules are evaluated in order, top to bottom. The first rule that matches an email wins. Processing stops there by default (a "continue processing" option is on the roadmap).

You can create, edit, reorder, and delete rules from the **Rules** page in the dashboard. Rules are stored in the database alongside everything else. Changes take effect immediately without a restart.

---

### Conditions

Each condition has three parts: a `field`, an `operator`, and a `value`. The dashboard gives you friendly dropdowns for all of these. The internal field and operator values are shown below for reference.

#### Sender fields

For the following examples, assume the sender address is `newsletter@mail.newsletter.example.com`.

| Dashboard label | Field value | What it matches | Example |
|---|---|---|---|
| Sender: full address | `sender` | The entire address | `newsletter@mail.newsletter.example.com` |
| Sender: local part (before @) | `sender_local` | Everything before the @ | `newsletter` |
| Sender: full domain | `sender_domain` | Everything after the @ | `mail.newsletter.example.com` |
| Sender: domain name | `sender_domain_name` | Subdomain and domain, no TLD | `mail.newsletter.example` |
| Sender: domain root | `sender_domain_root` | Registered domain only, no subdomain, no TLD | `example` |
| Sender: TLD | `sender_domain_tld` | Top-level domain only | `com` |

#### Recipient fields

The same six options exist for the recipient address, using `recipient` instead of `sender` in the drowndown:

`recipient`, `recipient_local`, `recipient_domain`, `recipient_domain_name`, `recipient_domain_root`, `recipient_domain_tld`

#### Message fields

| Dashboard label | Field value | What it matches |
|---|---|---|
| Subject | `subject` | The email subject line |
| Raw headers | `raw_headers` | All raw email headers (useful for `List-ID`, `X-Mailer`, etc.) |

#### Attachment fields

| Dashboard label | Field value | What it matches |
|---|---|---|
| Attachment: file name | `attachment_name` | The full filename (e.g. `invoice.pdf`) |
| Attachment: extension | `attachment_extension` | Just the extension (e.g. `pdf`, `exe`) |
| Attachment: content type | `attachment_content_type` | The MIME type (e.g. `application/pdf`) |

#### Spam score

| Dashboard label | Field value | What it matches |
|---|---|---|
| rspamd score | `rspamd_score` | The numeric spam score from rspamd |

---

### Operators

**For all text fields:**

| Dashboard label | Operator value | What it does |
|---|---|---|
| equals | `equals` | Exact match |
| does not equal | `not_equals` | Does not exactly match |
| contains | `contains` | Value appears anywhere in the field |
| does not contain | `not_contains` | Value does not appear in the field |
| is empty | `is_empty` | Field is blank or missing (no value needed) |

**For rspamd score:**

| Dashboard label | Operator value | What it does |
|---|---|---|
| greater than | `greater_than` | Score is above the value |
| less than | `less_than` | Score is below the value |
| greater than or equal | `greater_than_or_equal` | Score is at or above the value |
| less than or equal | `less_than_or_equal` | Score is at or below the value |

**A note on text matching:** When using `sender_local`, `sender_domain_name`, `sender_domain_root`, and their `recipient_*` equivalents, boxwatchr strips non-alphanumeric characters before comparing. This means `no.reply` and `noreply` both match if you search for `noreply`. This is intentional. It helps you match senders even when their address uses dots or dashes in different ways.

---

### Actions

Each action has a `type`. The `move` action also requires a `destination`.

| Dashboard label | Action type | What it does |
|---|---|---|
| Move to folder | `move` | Moves the email to the specified folder |
| Mark as read | `mark_read` | Marks the email as read |
| Mark as unread | `mark_unread` | Marks the email as unread |
| Flag message | `flag` | Flags/stars the email |
| Remove flag | `unflag` | Removes the flag/star |
| Submit to rspamd as spam | `learn_spam` | Submits the email to rspamd for spam training |
| Submit to rspamd as ham | `learn_ham` | Submits the email to rspamd as a good message |

**Move to folder** is the only "terminal" action. Once an email is moved, processing stops. You can still combine it with other actions like `mark_read` or `learn_spam` in the same rule and those will run before the move.

---

### Running a rule manually

On the Rules page, each rule has a **Run** button. This applies that rule to all emails currently in your watched folder that are also in the database. It's a great way to catch up on emails that arrived before boxwatchr was running, or to test a new rule against your existing mail.

---

## Dry Run mode

Dry Run is your safety net. When it's enabled:

- boxwatchr still monitors your inbox in real time
- It still evaluates every email against your rules
- It still scores emails with rspamd
- It does **not** move, mark, flag, or otherwise touch any emails
- It does **not** submit anything to rspamd for learning
- It logs exactly what it would have done, so you can review it in the dashboard

This is how you should start out. Run it in dry run mode for a day or two, watch the Emails page, and verify that your rules are matching what you expect. Once you're satisfied, turn dry run off in Config.

### First-run workflow in Dry Run mode

Here is the recommended order of operations when you are starting fresh with Dry Run enabled (after you have completed the initial setup):

1. **Start the container.** boxwatchr connects to your inbox, scans all existing messages, scores them with rspamd, and logs them to the database. At this point no rules exist yet, so every email is logged with "No rule matched."

2. **Create your rules.** Go to the Rules page and build your rules. The emails already in the database will be unaffected by this because they were processed before the rules existed.

3. **Test your rules against your existing mail.** You have two options:

   - **Run Rule (easiest):** On the Rules page, click **Run Rule** on each rule in order from top to bottom. Each run evaluates that rule against every email currently in your watched folder and writes a `[DRY RUN]` note showing what would have happened. Do them in order because that is how they will run in production.

   - **Restart the container:** On startup, boxwatchr automatically re-evaluates all unprocessed emails against your current rules in priority order exactly as the live pipeline would behave. This gives you the most realistic picture of how your rules interact with each other.

4. **Review the results.** Go to the Emails page and read the notes column. Each email shows which rule matched and what action would have been taken. If something looks wrong, check the Logs page for the detailed condition trace for that email.

5. **Adjust your rules and repeat** until you are happy with the results.

6. **Disable Dry Run** in Config. From this point forward, boxwatchr will act on your emails for real.

One important thing to know: emails that were processed in dry run mode are **not** retroactively submitted to rspamd for learning if you later turn dry run off. The raw message body isn't stored, so that ship has sailed. This is fine. Those emails will eventually cycle out of your mailbox anyway.

---

## Spam scoring

Every email that comes through gets scored by rspamd. The score shows up in the Emails list and on the detail page for each email.

The score is a number where higher means more likely to be spam. rspamd looks at things like:

- DNS blocklists (Spamhaus, URIBL, DBL, and others)
- Bayesian filter trained on your own mail
- Header analysis
- URL analysis
- ...and a lot more

You don't need to configure rspamd directly. It's running inside the container and boxwatchr handles talking to it. The only thing you control is what to do with the score, usually through a rule like "if score is above 6, treat it as spam."

### Bayesian training

The spam engine gets smarter when you train it. You can train it by adding a `learn_spam` or `learn_ham` action to a rule, and every email that matches that rule automatically gets submitted for training.

Bayesian data is stored on your server at `data/redis/` and persists across container restarts. Redis writes it to disk within 60 seconds of any change.

---

## The dashboard pages

### Dashboard

The landing page shows you aggregate stats:
- Total emails processed
- How many have been trained as spam
- How many have been trained as ham
- Pending emails (scored but not yet fully processed)
- A histogram of spam scores across all your mail
- A table showing how many times each rule has matched

### Emails

A full list of every email that came through, newest first. You can see the sender, subject, date, spam score, which rule matched, and what happened. Click any email to see full details.

### Email detail

Shows everything about a specific email:
- All the headers
- Attachments
- Spam score
- Which rule matched (if any)
- Every action that was taken (or would have been taken in dry run)
- The full action history if you've manually marked it as spam/ham
- The log entries tied to that specific email

### Rules

Create, edit, delete, and reorder your rules. The order matters. Rules are evaluated top to bottom and the first match wins.

### Logs

System logs, newest first. Filterable by log level and date range. Useful for debugging if something isn't behaving the way you expect.

### Config

Change any of your account and application settings here. All changes take effect immediately without restarting the container. If you change the IMAP credentials or watch folder, the connection reconnects automatically. If you change the web password, you are logged out immediately.

There is no "re-run setup" button. The Config page has all the same fields as the setup wizard.

---

## Ports reference

| Port | What it is |
|---|---|
| `8143` | boxwatchr web dashboard |
| `11334` | rspamd web interface (optional, password protected) |

---

## rspamd web interface

The rspamd controller is exposed on port 11334. This is an optional extra. You don't need it to use boxwatchr, but it lets you dig into rspamd directly if you're curious or debugging.

If you set `RSPAMD_PASSWORD` in your `.env`, use that password to log in. If you didn't set one, boxwatchr generated a random password at startup and printed it to the container logs. Run `docker compose logs boxwatchr | grep "rspamd web interface"` to find it. The password changes every restart, so set one in your `.env` if you want consistent access. boxwatchr still talks to rspamd internally either way.

---

## Environment variables reference

These go in `config/.env` and control container-level behavior. Everything else is configured through the web dashboard.

| Variable | Default | Description |
|---|---|---|
| `PUID` | `99` | User ID to run as inside the container |
| `PGID` | `100` | Group ID to run as inside the container |
| `TZ` | `UTC` | Timezone used for display. Logs are stored in UTC and converted at render time. |
| `RSPAMD_PASSWORD` | *(random)* | Password for the rspamd web interface on port 11334. Randomized at startup if not set. |

---

## Data persistence

boxwatchr stores everything in two folders on your host:

- `config/` contains your `.env` file. You can optionally create `rspamd/local.d/` and place `.conf` files there to override rspamd defaults, but no additional overrides have been tested with boxwatchr. Incorrect rspamd configuration can affect scoring, Bayesian learning, or cause rspamd to fail at startup. Proceed with caution.
- `data/` contains the SQLite database (`boxwatchr.db`) and Redis Bayesian data (`redis/`). Your rules and account settings are stored in the database alongside your email history.

**Back these up.** The database has your entire email processing history. The Redis data has your Bayesian training. Losing it means rspamd starts fresh.

---

## Troubleshooting

**The dashboard won't load**

Give it a minute after first startup. rspamd and the DNS resolver take a moment to initialize. Check `docker compose logs boxwatchr -f` to follow what's happening.

**My IMAP test connection fails**

Double-check the host, port, and TLS mode. For Gmail, use `imap.gmail.com`, port `993`, SSL, and make sure you're using an **App Password** if you have 2-factor authentication enabled. Google requires this because your regular account password won't work for IMAP. For most providers, SSL on port 993 is correct.

**Emails aren't being processed**

Check the Logs page first. Something is almost certainly logged there. Common issues:

- Dry Run is on (this is expected behavior, not a bug)
- No rules are matching. Check the Emails page. If the email shows up there, boxwatchr saw it. If no rule matched, the matched rule column will be empty.
- The IMAP connection dropped and didn't reconnect. Check docker logs.

**A rule isn't matching what I expect**

Use the **Run** button on the rule to test it against your existing mail. If you need to see condition-level evaluation detail, set the log level to DEBUG in Config and check the Logs page for the email in question.

**The spam score is always 0.0**

This usually means rspamd isn't running or the DNS resolver isn't working. Check `docker compose logs boxwatchr` for health check failures. rspamd needs working DNS to query blocklists, which is why boxwatchr includes its own internal resolver.

**I lost my web password**

Stop the container, open the database file at `data/boxwatchr.db` with any SQLite tool, and delete the `web_password` row from the `config` table. With no password set, the dashboard is accessible without logging in.

---

## A note on email security

boxwatchr stores your IMAP password encrypted in the database. It's not stored in plain text. The encryption key is stored in `data/secret.key` on your server, so anyone with full access to your server could theoretically recover it. Use this on a server you control and trust.

The rspamd HTTP connection is intentionally plain HTTP on localhost. There's no need for TLS when both services are on the same machine.

### Reverse proxy

Running boxwatchr behind a reverse proxy is strongly recommended if your dashboard is reachable from outside your home network. A reverse proxy lets you:

- Serve the dashboard over HTTPS with a real certificate (e.g. `https://boxwatchr.example.com`)
- Use your own domain instead of a raw IP address and port
- Add HTTP basic authentication at the proxy level as an extra layer on top of (or instead of) the built-in web password

Popular options include Nginx Proxy Manager, Caddy, Traefik, and nginx. Setup varies by choice and is outside the scope of this documentation.

One note on authentication: if you use a reverse proxy project that supports single sign-on (SSO) or identity-aware proxy features, passing those authentication headers through to boxwatchr has not been tested. The built-in web password is the supported authentication method.
