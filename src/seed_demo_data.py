"""
Load sample study groups into study_more.db.

    python seed_demo_data.py

Demo login: demo@example.com / test123
"""

import sqlite3

from werkzeug.security import generate_password_hash

import app as app_module
from demo_data import USERS, insert_demo_data

DEMO_PASSWORD = "test123"


def main():
    app_module.init_db()

    conn = sqlite3.connect("study_more.db")
    try:
        insert_demo_data(conn)

        # demo_data.py holds placeholder passwords so the tests can skip werkzeug.
        hashed = generate_password_hash(DEMO_PASSWORD)
        conn.executemany(
            "UPDATE users SET password = ? WHERE id = ?",
            [(hashed, user[0]) for user in USERS],
        )
        conn.commit()
    finally:
        conn.close()

    print("Sample data loaded into study_more.db")
    print(f"Log in as demo@example.com with the password {DEMO_PASSWORD}")


if __name__ == "__main__":
    main()
