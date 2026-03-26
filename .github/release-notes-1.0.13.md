## Fixed

- Logout form was sending the CSRF token under the field name `csrf_token` instead of `_csrf_token`, causing every logout attempt to return 403. (#46)

- Logout `<button>` now has `background: none; border: none` in `.site-nav-logout` to strip browser default button chrome. (#46)
