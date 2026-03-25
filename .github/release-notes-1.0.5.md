## Added

- Added version number to the navbar next to the brand. Reads the VERSION file baked into the image at startup.
- Added update check: the dashboard fetches the latest version from GitHub once per browser session and shows a toast in the bottom right if a newer version is available. Includes a changelog link, a session-dismiss X button, and a per-version "Don't show again" option stored in localStorage.
- Added Check for Updates toggle to the config page. When disabled, no outbound request to GitHub is made and the toast is never shown.
