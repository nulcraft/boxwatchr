## Fixed

- Web dashboard was hardcoded to port 80 internally and the Dockerfile exposed the wrong port (8080). Dashboard now defaults to port 8143 and is configurable via `WEB_PORT`. (#74)

- `_check_web()` in the health monitor was hardcoded to port 80, meaning it would always report the web service as down after the port change. (#76)

- `docker-compose.yml` port mapping was `8143:80` (wrong container port) and the healthcheck used a hardcoded port 80. (#77)

- Navbar hamburger menu appeared too late (md breakpoint, 768px), causing nav links to overflow the navbar background before collapsing. (#73)

## Added

- `WEB_PORT` environment variable controls the internal port the web dashboard listens on, defaulting to 8143. (#74)

- Web service startup now has its own labeled banner in the container startup output, consistent with all other services. (#76)

## Changed

- Training moved before Logs in the navigation bar. (#77)

- GitHub Actions updated to Node.js 24 compatible versions. (#72)
