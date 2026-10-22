"""Local-only password reset: python -m app.admin USERNAME."""

import argparse
import getpass

from . import auth


def main():
    parser = argparse.ArgumentParser(
        description="Reset a user's password and invalidate sessions"
    )
    parser.add_argument("username")
    args = parser.parse_args()
    password = getpass.getpass("New password (10–128 characters): ")
    if not 10 <= len(password) <= 128:
        parser.error("Password must have 10–128 characters")
    if password != getpass.getpass("Repeat password: "):
        parser.error("Passwords do not match")
    auth.initialize()
    with auth.connection() as db:
        row = db.execute(
            "SELECT id FROM users WHERE username=?", (args.username.lower(),)
        ).fetchone()
        if not row:
            parser.error("User not found")
        db.execute(
            "UPDATE users SET password=? WHERE id=?",
            (auth.password_hash(password), row["id"]),
        )
        db.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
    print("Password reset. All existing sessions revoked.")


if __name__ == "__main__":
    main()
