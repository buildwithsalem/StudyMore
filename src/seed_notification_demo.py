"""
Sample activity for trying the Notification Center.

    python seed_demo_data.py            (first: the sample users and groups)
    python seed_notification_demo.py    (then: this file)

Log in as demo@example.com with the password test123 and open Notifications.

Every notification here is created by the real features, through the same
routes a student would use, so the demo also shows the features working
together:
  - Sam Chen invites Demo Student to Algorithms Problem Set (still waiting)
  - Jordan Blake and Casey Nguyen reply to Demo Student's Midterm Prep post
  - Taylor Brooks accepts and Morgan Lee declines Demo Student's invitations
  - Morgan Lee invites Demo Student to Linked List Practice, then cancels it
Afterwards the timestamps are spread out so the list reads like a real week.
"""

import sqlite3
from datetime import datetime, timedelta

import app as app_module
from invitations import TIME_FORMAT

DEMO_GROUP_NAME = "Iteration 3 Demo Prep"

# Start of the message -> (how long ago it arrived, already read?)
TIMELINE = {
    "Sam Chen invited you": (timedelta(minutes=10), 0),
    "Jordan Blake replied": (timedelta(hours=2), 0),
    "Taylor Brooks accepted": (timedelta(hours=3), 0),
    "Casey Nguyen replied": (timedelta(days=1, hours=2), 1),
    "Morgan Lee declined": (timedelta(days=2), 1),
    "Morgan Lee invited you": (timedelta(days=4), 0),
}


def main():
    database = app_module.app.config["DATABASE"]
    app_module.init_db(database)
    conn = sqlite3.connect(database)
    if conn.execute("SELECT 1 FROM users WHERE email = 'demo@example.com'").fetchone() is None:
        print("Run python seed_demo_data.py first.")
        return
    if conn.execute("SELECT 1 FROM study_groups WHERE group_name = ?", (DEMO_GROUP_NAME,)).fetchone():
        print("Notification demo already loaded. Log in as demo@example.com / test123.")
        return

    # A group Demo Student created, so Demo Student can send invitations.
    meeting = datetime.now() + timedelta(days=2)
    cursor = conn.execute(
        """INSERT INTO study_groups (creator_user_id, group_name, course_code, section,
               description, meeting_type, meeting_date, meeting_time, location,
               meeting_link, max_members, status)
           VALUES (1, ?, 'CSE 3311', '001', 'Rehearse the iteration 3 demo.', 'In-Person',
                   ?, '18:00', 'Central Library 315', NULL, 6, 'open')""",
        (DEMO_GROUP_NAME, meeting.strftime("%Y-%m-%d")),
    )
    demo_group = cursor.lastrowid
    conn.execute("INSERT OR IGNORE INTO group_memberships (user_id, group_id) VALUES (1, ?)", (demo_group,))
    conn.commit()

    client = app_module.app.test_client()

    def act_as(user_id, name):
        with client.session_transaction() as s:
            s.clear()
            s["user_id"] = user_id
            s["user_name"] = name

    def newest_invitation_id():
        return conn.execute("SELECT MAX(id) FROM group_invitations").fetchone()[0]

    def newest_post_id():
        return conn.execute("SELECT MAX(id) FROM discussion_posts").fetchone()[0]

    # Morgan Lee (7) invites Demo Student to Linked List Practice (group 7), then cancels.
    act_as(7, "Morgan Lee")
    client.post("/group/7/invite", data={"email": "demo@example.com"})
    cancelled_invite = newest_invitation_id()
    client.post(f"/invitations/{cancelled_invite}/cancel")

    # Demo Student invites Morgan Lee (declines) and Taylor Brooks (accepts).
    act_as(1, "Demo Student")
    client.post(f"/group/{demo_group}/invite", data={"email": "morgan@example.com"})
    morgan_invite = newest_invitation_id()
    client.post(f"/group/{demo_group}/invite", data={"email": "taylor@example.com"})
    taylor_invite = newest_invitation_id()
    act_as(7, "Morgan Lee")
    client.post(f"/invitations/{morgan_invite}/decline")
    act_as(6, "Taylor Brooks")
    client.post(f"/invitations/{taylor_invite}/accept")

    # Demo Student asks a question in Midterm Prep (group 3); two members reply.
    act_as(1, "Demo Student")
    client.post("/group/3/discussions", data={
        "content": "Can someone explain integration by parts with a table? I keep mixing up the signs."})
    post_id = newest_post_id()
    act_as(8, "Casey Nguyen")
    client.post(f"/group/3/discussions/{post_id}/replies", data={
        "content": "Alternate plus and minus down the column. Bring your notes Saturday and I will show you."})
    act_as(4, "Jordan Blake")
    client.post(f"/group/3/discussions/{post_id}/replies", data={
        "content": "I put a worked example in the group folder, page 3."})

    # Sam Chen (5) invites Demo Student to Algorithms Problem Set (group 4).
    act_as(5, "Sam Chen")
    client.post("/group/4/invite", data={"email": "demo@example.com"})

    # Spread the times out so the list reads like a real week.
    now = datetime.now()
    for note_id, message in conn.execute(
        "SELECT id, message FROM notifications WHERE user_id = 1"
    ).fetchall():
        for start, (age, is_read) in TIMELINE.items():
            if message.startswith(start):
                conn.execute(
                    "UPDATE notifications SET created_at = ?, is_read = ? WHERE id = ?",
                    ((now - age).strftime(TIME_FORMAT), is_read, note_id),
                )
    conn.execute(
        "UPDATE group_invitations SET created_at = ? WHERE id = ?",
        ((now - timedelta(days=4)).strftime(TIME_FORMAT), cancelled_invite),
    )
    conn.commit()
    conn.close()
    print("Notification demo loaded. Log in as demo@example.com / test123 and open Notifications.")


if __name__ == "__main__":
    main()
