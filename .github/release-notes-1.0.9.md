## Added

- Added DISCLOSURES.md covering privacy, credential storage, AI assistance, third-party software licenses, and security vulnerability reporting. (#32)

## Fixed

- Fixed version check reporting a stale version by switching from reading the raw VERSION file on the main branch to the GitHub Releases API, which always reflects the latest published release. (#33)

## Changed

- Renamed rule condition field labels for clarity: "local part (before @)" to "Username", "domain name" to "Subdomain + domain", "domain root" to "Domain (no subdomain)". Removed redundant Sender/Recipient prefix from dropdown options. (#36)
- Made the version number in the navbar more prominent and adjusted the logo gap.

## Removed

- Removed PyYAML dependency, which was no longer used. (#35)
