## Changed

- Replaced the repeated `get_connection() / try / finally conn.close()` pattern across `database.py` and all web modules with a `_db()` context manager, exported as `db_connection` for use outside the module.
