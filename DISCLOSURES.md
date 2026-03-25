# Disclosures

This file covers privacy, credential storage, AI assistance, third-party software licenses, and security reporting for boxwatchr.

---

## Privacy and Data Handling

boxwatchr runs entirely on your own infrastructure. It connects to services you configure (your IMAP server) and services bundled inside the container (rspamd, Redis, Unbound). It has no connection to any server operated by nulcraft.

- No analytics, telemetry, usage tracking, or error reporting of any kind is collected.
- nulcraft has no visibility into who is running boxwatchr, how it is configured, or what email it processes.
- All data, including emails, processing logs, and configuration, stays on the host where the container runs.

The only connection boxwatchr initiates to a server outside your own infrastructure is a periodic check against the GitHub Releases API to detect whether a newer version is available. This request goes to GitHub's servers, not nulcraft's, and is subject to [GitHub's Privacy Statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement). No account data, email content, or identifying information is sent as part of this request. The version check can be disabled in the application config if you prefer boxwatchr to make no external connections beyond your own mail server.

---

## Credential Storage

All passwords stored by boxwatchr are protected at rest.

- IMAP passwords are encrypted using Fernet symmetric encryption (provided by the `cryptography` library) before being written to the SQLite database. They are never stored in plaintext.
- The web dashboard password is stored as a hash, never in plaintext.

The encryption key is derived from the host environment and never transmitted outside the container.

---

## AI Assistance

Claude Code has been consulted during the development of boxwatchr as a coding assistant, primarily for conventions, logic review, and implementation guidance on specific, narrowly scoped tasks. At no point has any AI tool been given free rein over the codebase. Every suggestion made by Claude Code was evaluated, decided on, and reviewed by a human before being accepted, and in most cases the resulting code was written or retyped by hand rather than applied directly.

All code published in this repository reflects deliberate human authorship and judgment. AI assistance was a tool, not a co-author.

---

## Third-Party Software

boxwatchr bundles the following third-party software. Each component is used as described and is covered by its own license.

### Python Dependencies

| Package | Version | License | Purpose |
|---|---|---|---|
| IMAPClient | 3.1.0 | BSD-3-Clause | IMAP connection management and IDLE push notifications |
| python-dotenv | 1.2.2 | BSD-3-Clause | Loads environment variables from the `.env` file at startup |
| watchdog | 6.0.0 | Apache 2.0 | Detects filesystem changes to hot-reload rules without a restart |
| Flask | 3.1.3 | BSD-3-Clause | Web framework for the dashboard |
| Werkzeug | 3.1.6 | BSD-3-Clause | WSGI toolkit and request/response layer (Flask dependency) |
| Jinja2 | 3.1.6 | BSD-3-Clause | HTML templating engine for dashboard pages (Flask dependency) |
| MarkupSafe | 3.0.3 | BSD-3-Clause | Safe HTML escaping (Jinja2 dependency) |
| itsdangerous | 2.2.0 | BSD-3-Clause | Secure session cookie signing (Flask dependency) |
| click | 8.3.1 | BSD-3-Clause | CLI argument parsing (Flask dependency) |
| colorama | 0.4.6 | BSD-3-Clause | ANSI color output support on Windows (click dependency) |
| blinker | 1.9.0 | MIT | Signal/event dispatch (Flask dependency) |
| requests | 2.32.5 | Apache 2.0 | HTTP client used to call the rspamd API and check for version updates |
| urllib3 | 2.6.3 | MIT | HTTP connection pooling (requests dependency) |
| certifi | 2026.2.25 | MPL 2.0 | Mozilla CA certificate bundle for TLS verification (requests dependency) |
| charset-normalizer | 3.4.6 | MIT | HTTP response charset detection (requests dependency) |
| idna | 3.11 | BSD-3-Clause | Internationalized domain name handling (requests dependency) |
| cryptography | 46.0.5 | Apache 2.0 / BSD | Fernet symmetric encryption for IMAP password storage |
| tldextract | 5.3.1 | BSD-3-Clause | Extracts the registrable domain from email sender addresses for rule matching |
| filelock | 3.25.2 | MIT | File locking for tldextract's local TLD cache (tldextract dependency) |
| requests-file | 3.0.1 | Apache 2.0 | `file://` URL support used by tldextract for its offline TLD list (tldextract dependency) |

### Bundled System Services

These services are installed into the Docker image at build time and run as background processes managed by Supervisor.

| Component | License | Purpose |
|---|---|---|
| Debian (trixie-slim) | Various (see [Debian license information](https://www.debian.org/legal/licenses/)) | Base container OS |
| rspamd | Apache 2.0 | Spam scoring engine; scores incoming messages and provides Bayes learning |
| Redis | BSD-3-Clause | In-memory data store used by rspamd to persist Bayes training data |
| Unbound | BSD-3-Clause | Local DNS resolver used by rspamd to query DNS blocklists without leaking queries to upstream resolvers |
| Supervisor | Repoze Public License | Process manager that starts and monitors all services inside the container |

---

## Security Vulnerabilities

To report a security vulnerability, please use [GitHub's private security advisory feature](https://github.com/nulcraft/boxwatchr/security/advisories/new) rather than opening a public issue. This keeps the details private until a fix is available.

---

## No Warranty

boxwatchr is provided as-is under the GNU General Public License v3.0. There is no warranty of any kind, express or implied. The full license text is in [LICENSE](LICENSE).
