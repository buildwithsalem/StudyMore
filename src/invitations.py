"""
Group Invitations & Member Management.

  - A group creator invites another StudyMore student by their UTA email.
  - The invited student accepts or declines from the Invitations page.
  - Accepting adds them to the group. The creator can cancel pending invitations.
  - Invitation activity creates notifications (see create_notification below).

Invitation status is stored as pending, accepted, declined, cancelled or expired.
"Expired" is mostly worked out when the invitation is read: a pending invitation
expires after INVITE_LIFETIME_DAYS, or as soon as its group has finished meeting
or been cancelled (same idea as the derived "Full" group status). The stored
value is only updated when the same student is invited again.
"""

import sqlite3
from datetime import datetime, timedelta

from flask import (
    Blueprint,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from db import get_db_connection
from search_query import CLOSED_STATUSES, derive_display_status, tidy_status
from student_profile import get_member_cards

invitations_bp = Blueprint("invitations", __name__, template_folder="templates")

INVITE_LIFETIME_DAYS = 7
HISTORY_LIMIT = 10
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------

def ensure_invitation_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS group_invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            inviter_id INTEGER NOT NULL,
            invitee_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            responded_at TEXT,
            FOREIGN KEY (group_id) REFERENCES study_groups(id),
            FOREIGN KEY (inviter_id) REFERENCES users(id),
            FOREIGN KEY (invitee_id) REFERENCES users(id)
        )
        """
    )
    # Database-level guard: at most one pending invitation per student per group,
    # even if two requests arrive at the same moment.
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS one_pending_invite_per_student
        ON group_invitations (group_id, invitee_id)
        WHERE status = 'pending'
        """
    )
    ensure_notifications_table(conn)


def ensure_notifications_table(conn):
    """
    Shared table for the Notification Center. Created here only if the
    Notification Center code hasn't created it yet.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            message TEXT NOT NULL,
            link_url TEXT,
            group_id INTEGER,
            related_id INTEGER,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )


def create_notification(conn, user_id, kind, message, link_url=None,
                        group_id=None, related_id=None):
    """
    The one function the invitation code calls to tell a student something.
    Skips the insert if the same student already has a notification of the
    same kind about the same item, so a repeated action can't pile them up.
    Does not commit; the caller commits together with its own change.
    """
    ensure_notifications_table(conn)
    if related_id is not None:
        duplicate = conn.execute(
            "SELECT 1 FROM notifications WHERE user_id = ? AND kind = ? AND related_id = ?",
            (user_id, kind, related_id),
        ).fetchone()
        if duplicate:
            return False
    conn.execute(
        """
        INSERT INTO notifications
            (user_id, kind, message, link_url, group_id, related_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, kind, message, link_url, group_id, related_id,
         datetime.now().strftime(TIME_FORMAT)),
    )
    return True


# --------------------------------------------------------------------------
# Rules (no Flask in here, so they are easy to test)
# --------------------------------------------------------------------------

def now_text(now=None):
    return (now or datetime.now()).strftime(TIME_FORMAT)


def invitation_status(row, now=None):
    """pending / accepted / declined / cancelled / expired for one joined row."""
    stored = row["status"]
    if stored != "pending":
        return stored
    now = now or datetime.now()
    try:
        created = datetime.strptime(row["created_at"], TIME_FORMAT)
    except (TypeError, ValueError):
        return "expired"
    if now - created > timedelta(days=INVITE_LIFETIME_DAYS):
        return "expired"
    group_status = derive_display_status(
        row["group_status"], 0, row["max_members"],
        row["meeting_date"], row["meeting_time"],
    )
    if group_status in CLOSED_STATUSES:
        return "expired"
    return "pending"


def member_count(conn, group_id):
    return conn.execute(
        "SELECT COUNT(*) FROM group_memberships WHERE group_id = ?", (group_id,)
    ).fetchone()[0]


def is_member(conn, group_id, user_id):
    return conn.execute(
        "SELECT 1 FROM group_memberships WHERE group_id = ? AND user_id = ?",
        (group_id, user_id),
    ).fetchone() is not None


def group_display_status(conn, group):
    return derive_display_status(
        group["status"], member_count(conn, group["id"]), group["max_members"],
        group["meeting_date"], group["meeting_time"],
    )


INVITATION_SELECT = """
    SELECT gi.id, gi.group_id, gi.inviter_id, gi.invitee_id, gi.status,
           gi.created_at, gi.responded_at,
           sg.group_name, sg.course_code, sg.section, sg.meeting_date,
           sg.meeting_time, sg.max_members, sg.status AS group_status,
           sg.creator_user_id,
           inviter.name AS inviter_name,
           invitee.name AS invitee_name, invitee.email AS invitee_email
    FROM group_invitations gi
    JOIN study_groups sg ON sg.id = gi.group_id
    JOIN users inviter ON inviter.id = gi.inviter_id
    JOIN users invitee ON invitee.id = gi.invitee_id
"""


def load_invitation(conn, invitation_id):
    return conn.execute(
        INVITATION_SELECT + " WHERE gi.id = ?", (invitation_id,)
    ).fetchone()


def pending_count_for(conn, user_id, now=None):
    rows = conn.execute(
        INVITATION_SELECT + " WHERE gi.invitee_id = ? AND gi.status = 'pending'",
        (user_id,),
    ).fetchall()
    return sum(1 for row in rows if invitation_status(row, now) == "pending")


def check_invite(conn, group, inviter_id, email):
    """
    Returns (invitee_row, error). Every reason an invitation can be refused
    lives here.
    """
    email = (email or "").strip().lower()
    if not email:
        return None, "Please enter the email of the student you want to invite."

    invitee = conn.execute(
        "SELECT id, name, email, email_verified FROM users WHERE email = ?", (email,)
    ).fetchone() if _has_verified_column(conn) else conn.execute(
        "SELECT id, name, email, 1 AS email_verified FROM users WHERE email = ?", (email,)
    ).fetchone()

    if invitee is None or not invitee["email_verified"]:
        return None, "No StudyMore student has joined with that email yet."
    if invitee["id"] == inviter_id:
        return None, "You're already in this group."
    if is_member(conn, group["id"], invitee["id"]):
        return None, f"{invitee['name']} is already a member of this group."

    status = group_display_status(conn, group)
    if status != "Open":
        reasons = {
            "Full": "This group is full. Raise the maximum members first, or wait for someone to leave.",
            "Completed": "This group has already finished meeting. Edit the meeting date to invite more students.",
            "Cancelled": "This group is cancelled. Reopen it to invite students.",
        }
        return None, reasons.get(status, "This group isn't open for invitations.")

    rows = conn.execute(
        INVITATION_SELECT + " WHERE gi.group_id = ? AND gi.invitee_id = ? AND gi.status = 'pending'",
        (group["id"], invitee["id"]),
    ).fetchall()
    if any(invitation_status(row) == "pending" for row in rows):
        return None, f"{invitee['name']} already has a pending invitation to this group."

    return invitee, None


def _has_verified_column(conn):
    return any(row[1] == "email_verified" for row in conn.execute("PRAGMA table_info(users)"))


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

def _login_redirect():
    return redirect(url_for("login"))


@invitations_bp.app_context_processor
def inject_pending_count():
    """Lets every page's menu show how many invitations are waiting."""
    user_id = session.get("user_id")
    if user_id is None:
        return {"pending_invite_count": 0}
    try:
        conn = get_db_connection()
        ensure_invitation_schema(conn)
        return {"pending_invite_count": pending_count_for(conn, user_id)}
    except sqlite3.Error:
        return {"pending_invite_count": 0}


def _manage_page(conn, group, user_id, error=None, info=None, status=200):
    ensure_invitation_schema(conn)
    rows = conn.execute(
        INVITATION_SELECT + " WHERE gi.group_id = ? ORDER BY gi.id DESC", (group["id"],)
    ).fetchall()
    pending, history = [], []
    for row in rows:
        item = dict(row)
        item["effective_status"] = invitation_status(row)
        (pending if item["effective_status"] == "pending" else history).append(item)
    members = get_member_cards(conn, group["id"], group["creator_user_id"])
    return (
        render_template(
            "manage_group.html",
            group=group,
            members=members,
            pending=pending,
            history=history[:HISTORY_LIMIT],
            display_status=group_display_status(conn, group),
            member_count=len(members),
            invite_lifetime_days=INVITE_LIFETIME_DAYS,
            error=error,
            info=info,
        ),
        status,
    )


def _creator_group(conn, group_id, user_id):
    """Returns (group, error_response). Only the creator may manage a group."""
    group = conn.execute(
        "SELECT * FROM study_groups WHERE id = ?", (group_id,)
    ).fetchone()
    if group is None:
        return None, ("Study group not found", 404)
    if group["creator_user_id"] != user_id:
        return None, ("Only the group creator can manage invitations", 403)
    return group, None


@invitations_bp.route("/group/<int:group_id>/manage")
def manage_group(group_id):
    user_id = session.get("user_id")
    if user_id is None:
        return _login_redirect()
    conn = get_db_connection()
    group, error = _creator_group(conn, group_id, user_id)
    if error:
        return error
    return _manage_page(conn, group, user_id, info=session.pop("manage_info", None))


@invitations_bp.route("/group/<int:group_id>/invite", methods=["POST"])
def invite(group_id):
    user_id = session.get("user_id")
    if user_id is None:
        return _login_redirect()
    conn = get_db_connection()
    group, error = _creator_group(conn, group_id, user_id)
    if error:
        return error
    ensure_invitation_schema(conn)

    invitee, problem = check_invite(conn, group, user_id, request.form.get("email"))
    if problem:
        return _manage_page(conn, group, user_id, error=problem, status=400)

    # check_invite found no live pending invitation, so any row still stored as
    # pending is an expired one. Close it out so it can't block the new one.
    conn.execute(
        "UPDATE group_invitations SET status = 'expired', responded_at = ? "
        "WHERE group_id = ? AND invitee_id = ? AND status = 'pending'",
        (now_text(), group_id, invitee["id"]),
    )
    try:
        cursor = conn.execute(
            "INSERT INTO group_invitations (group_id, inviter_id, invitee_id, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (group_id, user_id, invitee["id"], now_text()),
        )
    except sqlite3.IntegrityError:
        conn.rollback()
        return _manage_page(
            conn, group, user_id,
            error=f"{invitee['name']} already has a pending invitation to this group.",
            status=400,
        )

    inviter_name = session.get("user_name") or "A student"
    create_notification(
        conn, invitee["id"], "group_invitation",
        f"{inviter_name} invited you to join {group['group_name']} ({group['course_code']}).",
        link_url=url_for("invitations.inbox"),
        group_id=group_id, related_id=cursor.lastrowid,
    )
    conn.commit()
    session["manage_info"] = f"Invitation sent to {invitee['name']}."
    return redirect(url_for("invitations.manage_group", group_id=group_id))


@invitations_bp.route("/invitations/<int:invitation_id>/cancel", methods=["POST"])
def cancel(invitation_id):
    user_id = session.get("user_id")
    if user_id is None:
        return _login_redirect()
    conn = get_db_connection()
    ensure_invitation_schema(conn)
    row = load_invitation(conn, invitation_id)
    if row is None:
        return "Invitation not found", 404
    if row["creator_user_id"] != user_id:
        return "Only the group creator can cancel invitations", 403

    group = conn.execute("SELECT * FROM study_groups WHERE id = ?", (row["group_id"],)).fetchone()
    if row["status"] != "pending":
        return _manage_page(
            conn, group, user_id,
            error="That invitation was already answered or cancelled.", status=400,
        )
    conn.execute(
        "UPDATE group_invitations SET status = 'cancelled', responded_at = ? "
        "WHERE id = ? AND status = 'pending'",
        (now_text(), invitation_id),
    )
    conn.commit()
    session["manage_info"] = f"Invitation to {row['invitee_name']} cancelled."
    return redirect(url_for("invitations.manage_group", group_id=row["group_id"]))


@invitations_bp.route("/invitations")
def inbox():
    user_id = session.get("user_id")
    if user_id is None:
        return _login_redirect()
    conn = get_db_connection()
    ensure_invitation_schema(conn)
    rows = conn.execute(
        INVITATION_SELECT + " WHERE gi.invitee_id = ? ORDER BY gi.id DESC", (user_id,)
    ).fetchall()
    pending, history = [], []
    for row in rows:
        item = dict(row)
        item["effective_status"] = invitation_status(row)
        if item["effective_status"] == "pending":
            item["member_count"] = member_count(conn, row["group_id"])
            pending.append(item)
        else:
            history.append(item)
    return render_template(
        "invitations.html",
        pending=pending,
        history=history[:HISTORY_LIMIT],
        error=session.pop("invite_error", None),
        info=session.pop("invite_info", None),
    )


def _respond(invitation_id, action):
    user_id = session.get("user_id")
    if user_id is None:
        return _login_redirect()
    conn = get_db_connection()
    ensure_invitation_schema(conn)
    row = load_invitation(conn, invitation_id)
    # Someone else's invitation looks exactly like one that doesn't exist.
    if row is None or row["invitee_id"] != user_id:
        return "Invitation not found", 404

    state = invitation_status(row)
    if state != "pending":
        messages = {
            "accepted": "You already accepted that invitation.",
            "declined": "You already declined that invitation.",
            "cancelled": "The group creator cancelled that invitation.",
            "expired": "That invitation has expired.",
        }
        session["invite_error"] = messages.get(state, "That invitation is no longer available.")
        return redirect(url_for("invitations.inbox"))

    group = conn.execute("SELECT * FROM study_groups WHERE id = ?", (row["group_id"],)).fetchone()

    if action == "decline":
        conn.execute(
            "UPDATE group_invitations SET status = 'declined', responded_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (now_text(), invitation_id),
        )
        create_notification(
            conn, row["inviter_id"], "invitation_declined",
            f"{session.get('user_name') or 'A student'} declined your invitation to {row['group_name']}.",
            link_url=url_for("invitations.manage_group", group_id=row["group_id"]),
            group_id=row["group_id"], related_id=invitation_id,
        )
        conn.commit()
        session["invite_info"] = f"You declined the invitation to {row['group_name']}."
        return redirect(url_for("invitations.inbox"))

    # Accept. The check and the insert happen under one write lock so two
    # people accepting the last seat can't both get in.
    conn.execute("BEGIN IMMEDIATE")
    already_in = is_member(conn, row["group_id"], user_id)
    if not already_in:
        status = group_display_status(conn, group)
        if status != "Open":
            conn.rollback()
            reason = "This group is full right now." if status == "Full" else "This group is no longer open."
            session["invite_error"] = reason + " The invitation stays here in case a spot opens up."
            return redirect(url_for("invitations.inbox"))
        conn.execute(
            "INSERT OR IGNORE INTO group_memberships (user_id, group_id) VALUES (?, ?)",
            (user_id, row["group_id"]),
        )
    conn.execute(
        "UPDATE group_invitations SET status = 'accepted', responded_at = ? "
        "WHERE id = ? AND status = 'pending'",
        (now_text(), invitation_id),
    )
    create_notification(
        conn, row["inviter_id"], "invitation_accepted",
        f"{session.get('user_name') or 'A student'} accepted your invitation to {row['group_name']}.",
        link_url=url_for("invitations.manage_group", group_id=row["group_id"]),
        group_id=row["group_id"], related_id=invitation_id,
    )
    conn.commit()
    session["invite_info"] = f"You joined {row['group_name']}."
    return redirect(url_for("group_details", group_id=row["group_id"]))


@invitations_bp.route("/invitations/<int:invitation_id>/accept", methods=["POST"])
def accept(invitation_id):
    return _respond(invitation_id, "accept")


@invitations_bp.route("/invitations/<int:invitation_id>/decline", methods=["POST"])
def decline(invitation_id):
    return _respond(invitation_id, "decline")
