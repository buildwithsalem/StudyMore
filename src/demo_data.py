"""Sample users, groups and memberships. Shared by the tests and the seeder."""

# The demo login is user 1. Passwords here are placeholders; seed_demo_data.py
# replaces them with real hashes.
USERS = [
    (1, "Demo Student", "demo@example.com", "demo-hash-1"),
    (2, "Alex Rivera", "alex@example.com", "demo-hash-2"),
    (3, "Priya Nair", "priya@example.com", "demo-hash-3"),
    (4, "Jordan Blake", "jordan@example.com", "demo-hash-4"),
    (5, "Sam Chen", "sam@example.com", "demo-hash-5"),
    (6, "Taylor Brooks", "taylor@example.com", "demo-hash-6"),
    (7, "Morgan Lee", "morgan@example.com", "demo-hash-7"),
    (8, "Casey Nguyen", "casey@example.com", "demo-hash-8"),
]

# One group for each case the search has to handle: open, full (4 of 4 while the
# column still says open), hybrid, completed, cancelled, and zero members.
GROUPS = [
    # id, creator, name, course, section, description, type, date, time,
    # location, link, max_members, status
    (1, 2, "Iteration 1 Review", "CSE 3311", "001",
     "Going over the iteration 1 deliverable and the screen flow diagrams.",
     "In-Person", "2026-11-12", "17:00", "ERB 128", None, 6, "open"),

    (2, 3, "Exam 1 Study Jam", "CSE 3320", "002",
     "Process scheduling and memory management practice problems.",
     "Online", "2026-11-13", "19:00", None, "https://uta.zoom.us/j/1234567890", 4, "open"),

    (3, 4, "Midterm Prep", "MATH 1426", "004",
     "Integration techniques. Meet in the library or join the call.",
     "Hybrid", "2026-11-15", "14:30", "Central Library 204",
     "https://uta.zoom.us/j/2233445566", 8, "open"),

    (4, 5, "Algorithms Problem Set", "CSE 3315", "001",
     "Working through the proofs on problem set 3 together.",
     "In-Person", "2026-11-11", "16:00", "PKH 105", None, 5, "open"),

    (5, 2, "Finals Review", "CSE 3311", "002",
     "Last review session before the final. This group has finished meeting.",
     "Online", "2026-05-05", "10:00", None, "https://uta.zoom.us/j/9988776655", 10, "completed"),

    (6, 6, "Weekend Sprint", "CSE 3320", "001",
     "Cancelled because the room booking fell through.",
     "In-Person", "2026-11-17", "11:00", "SEIR 198", None, 6, "cancelled"),

    (7, 7, "Linked List Practice", "CSE 3318", "003",
     "Pointer heavy practice problems. Nobody has joined yet.",
     "Online", "2026-11-14", "20:00", None, "https://uta.zoom.us/j/5544332211", 5, "open"),
]

# (user_id, group_id)
MEMBERSHIPS = [
    (1, 1), (2, 1),
    (3, 2), (5, 2), (6, 2), (7, 2),
    (1, 3), (4, 3), (8, 3),
    (5, 4),
    (2, 5), (3, 5), (4, 5), (5, 5), (6, 5), (7, 5), (8, 5),
    (6, 6), (7, 6),
]


def insert_demo_data(conn):
    """Insert the sample rows. Safe to run more than once."""
    # Clear memberships for the sample groups first so reseeding does not
    # change the member counts. Only the sample group ids are touched.
    sample_group_ids = tuple(group[0] for group in GROUPS)
    placeholders = ", ".join("?" for _ in sample_group_ids)
    conn.execute(
        f"DELETE FROM group_memberships WHERE group_id IN ({placeholders})",
        sample_group_ids,
    )

    conn.executemany(
        "INSERT OR REPLACE INTO users (id, name, email, password) VALUES (?, ?, ?, ?)",
        USERS,
    )
    conn.executemany(
        """
        INSERT OR REPLACE INTO study_groups
            (id, creator_user_id, group_name, course_code, section, description,
             meeting_type, meeting_date, meeting_time, location, meeting_link,
             max_members, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        GROUPS,
    )
    conn.executemany(
        "INSERT OR IGNORE INTO group_memberships (user_id, group_id) VALUES (?, ?)",
        MEMBERSHIPS,
    )
    conn.commit()
