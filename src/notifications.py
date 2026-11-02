"""
Notification Center.

One page where a student sees every important update about their StudyMore
activity, newest first, with read and unread status.

How the pieces fit together
  - Other features WRITE notifications by calling invitations.create_notification():
        Group Invitations  -> group_invitation, invitation_accepted, invitation_declined
        Discussion Board   -> discussion_reply
    That function already skips duplicates (same student, same kind, same item).
  - This module READS them: it lists them, tracks read and unread, sends the
    student to the right invitation, group or discussion, and gives the menu
    its unread count.

Special cases handled here
  - A student can only see or change their own notifications. Anyone else's
    id gets a 404, so ids cannot be probed.
  - Links are only followed when they stay inside StudyMore (no open redirect).
  - If the page a notification points to is gone (group deleted, student left
    the group, reply removed) the card says so instead of opening an error page.
  - An invitation notification only counts as unread while the invitation is
    still waiting for an answer. Once it is accepted, declined, cancelled or
    expired there is nothing left to do, so it stops adding to the badge.
    Like the invitation "expired" status, this is worked out when the data is
    read, so no other feature has to update the notifications table.
"""

import sqlite3
from datetime import datetime

from flask import (
    Blueprint,
    abort,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from db import get_db_connection
from invitations import (
    TIME_FORMAT,
    ensure_invitation_schema,
    invitation_status,
    is_member,
    load_invitation,
)

notifications_bp = Blueprint("notifications", __name__, template_folder="templates")

PAGE_LIMIT = 50
FILTERS = ("all", "unread")

# kind -> (label on the card, CSS modifier for the colored tag)
KINDS = {
    "group_invitation": ("Invitation", "invite"),
    "invitation_accepted": ("Accepted", "accepted"),
    "invitation_declined": ("Declined", "declined"),
    "discussion_reply": ("Reply", "reply"),
}
DEFAULT_KIND = ("Update", "update")

# What an invitation notification says once the invitation is settled.
INVITATION_NOTES = {
    "accepted": "You already accepted this invitation.",
    "declined": "You already declined this invitation.",
    "cancelled": "The group creator cancelled this invitation.",
    "expired": "This invitation has expired.",
}


# --------------------------------------------------------------------------
# Table
# --------------------------------------------------------------------------

def ensure_notification_schema(conn):
    """
    The notifications table is shared with the features that write to it, so it
    is created by invitations.ensure_invitation_schema (which also creates the
    group_invitations table this module reads). This adds the index that keeps
    one student's list fast.
    """
    ensure_invitation_schema(conn)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS notifications_by_user "
        "ON notifications (user_id, is_read, created_at)"
    )


# --------------------------------------------------------------------------
# Rules (no Flask in here, so they are easy to test)
# --------------------------------------------------------------------------

def safe_link(link_url):
    """The link if it stays inside StudyMore ("/group/4"), otherwise None."""
    if not link_url or not link_url.startswith("/"):
        return None
    if link_url.startswith("//") or "\\" in link_url:
        return None
    return link_url


def time_ago(created_at, now=None):
    """'Just now', '5 min ago', '3 hr ago', 'Yesterday', '4 days ago' or 'Oct 28'."""
    try:
        created = datetime.strptime(created_at, TIME_FORMAT)
    except (TypeError, ValueError):
        return ""
    now = now or datetime.now()
    seconds = (now - created).total_seconds()
    if seconds < 60:
        return "Just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} hr ago"
    days = (now.date() - created.date()).days
    if days == 1:
        return "Yesterday"
    if days < 7:
        return f"{days} days ago"
    label = f"{created.strftime('%b')} {created.day}"
    return label if created.year == now.year else f"{label}, {created.year}"


def invitation_state(conn, invitation_id, now=None):
    """pending / accepted / declined / cancelled / expired, using the invitation rules."""
    row = load_invitation(conn, invitation_id)
    return invitation_status(row, now) if row is not None else "cancelled"


def is_unread(conn, note, now=None):
    """Unread, unless it is an invitation that can no longer be answered."""
    if note["is_read"]:
        return False
    if note["kind"] == "group_invitation":
        return invitation_state(conn, note["related_id"], now) == "pending"
    return True


def unread_count(conn, user_id, now=None):
    rows = conn.execute(
        "SELECT kind, related_id, is_read FROM notifications WHERE user_id = ? AND is_read = 0",
        (user_id,),
    ).fetchall()
    return sum(1 for row in rows if is_unread(conn, row, now))


def check_target(conn, user_id, note, now=None):
    """
    Returns (can_open, status_note) for the page this notification points to.
    status_note is a short explanation shown on the card, or None.
    Checked when the list is shown and again when the student clicks Open,
    because the group or invitation can change in between.
    """
    if safe_link(note["link_url"]) is None:
        return False, None

    group_id = note["group_id"]
    if group_id is not None:
        group = conn.execute(
            "SELECT creator_user_id FROM study_groups WHERE id = ?", (group_id,)
        ).fetchone()
        if group is None:
            return False, "This study group no longer exists."
        if note["kind"] == "discussion_reply" and not is_member(conn, group_id, user_id):
            return False, "You are no longer a member of this group."
        if note["kind"] in ("invitation_accepted", "invitation_declined") \
                and group["creator_user_id"] != user_id:
            return False, "You no longer manage this group."

    if note["kind"] == "discussion_reply" and conn.execute(
        "SELECT 1 FROM discussion_replies WHERE id = ?", (note["related_id"],)
    ).fetchone() is None:
        return False, "This reply was removed."

    if note["kind"] == "group_invitation":
        state = invitation_state(conn, note["related_id"], now)
        if state != "pending":
            # Still opens: the Invitations page lists earlier invitations.
            return True, INVITATION_NOTES.get(state)

    return True, None


def list_notifications(conn, user_id, show="all", now=None, limit=PAGE_LIMIT):
    """The student's newest notifications, as dicts ready for the template."""
    if show == "unread":
        rows = conn.execute(
            "SELECT * FROM notifications WHERE user_id = ? AND is_read = 0 "
            "ORDER BY created_at DESC, id DESC",
            (user_id,),
        ).fetchall()
        rows = [row for row in rows if is_unread(conn, row, now)][:limit]
    else:
        rows = conn.execute(
            "SELECT * FROM notifications WHERE user_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()

    items = []
    for row in rows:
        item = dict(row)
        item["label"], item["tag"] = KINDS.get(row["kind"], DEFAULT_KIND)
        item["when"] = time_ago(row["created_at"], now)
        item["unread"] = is_unread(conn, row, now)
        item["can_open"], item["status_note"] = check_target(conn, user_id, row, now)
        # A settled invitation never counts as unread again, so hide that button.
        item["can_mark_unread"] = not (row["kind"] == "group_invitation" and item["status_note"])
        items.append(item)
    return items


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@notifications_bp.before_request
def require_login():
    if session.get("user_id") is None:
        return redirect(url_for("login"))


@notifications_bp.app_context_processor
def inject_unread_count():
    """Lets every page's menu show how many notifications are unread."""
    user_id = session.get("user_id")
    if user_id is None:
        return {"unread_notification_count": 0}
    try:
        conn = get_db_connection()
        ensure_notification_schema(conn)
        return {"unread_notification_count": unread_count(conn, user_id)}
    except sqlite3.Error:
        return {"unread_notification_count": 0}


def _connection():
    conn = get_db_connection()
    ensure_notification_schema(conn)
    return conn


def _own_notification(conn, notification_id, user_id):
    """The notification if it belongs to this student, otherwise a 404."""
    row = conn.execute(
        "SELECT * FROM notifications WHERE id = ? AND user_id = ?",
        (notification_id, user_id),
    ).fetchone()
    if row is None:
        abort(404)
    return row


def _back_to_list():
    """Return to the list, keeping the All or Unread filter the student was on."""
    show = request.form.get("show", "all")
    return redirect(url_for("notifications.center", show=show if show in FILTERS else "all"))


@notifications_bp.route("/notifications")
def center():
    user_id = session["user_id"]
    show = request.args.get("show", "all")
    if show not in FILTERS:
        show = "all"
    conn = _connection()
    total = conn.execute(
        "SELECT COUNT(*) FROM notifications WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    return render_template(
        "notifications.html",
        items=list_notifications(conn, user_id, show),
        show=show,
        total=total,
        unread=unread_count(conn, user_id),
        info=session.pop("notification_info", None),
    )


@notifications_bp.route("/notifications/<int:notification_id>/open", methods=["POST"])
def open_notification(notification_id):
    """Mark it read, then go to the invitation, group or discussion it is about."""
    user_id = session["user_id"]
    conn = _connection()
    note = _own_notification(conn, notification_id, user_id)
    conn.execute("UPDATE notifications SET is_read = 1 WHERE id = ?", (note["id"],))
    conn.commit()
    can_open, status_note = check_target(conn, user_id, note)
    if not can_open:
        session["notification_info"] = status_note or "There is no page to open for this notification."
        return _back_to_list()
    return redirect(safe_link(note["link_url"]))


@notifications_bp.route("/notifications/<int:notification_id>/read", methods=["POST"])
def mark_read(notification_id):
    return _set_read(notification_id, 1)


@notifications_bp.route("/notifications/<int:notification_id>/unread", methods=["POST"])
def mark_unread(notification_id):
    return _set_read(notification_id, 0)


def _set_read(notification_id, is_read):
    conn = _connection()
    note = _own_notification(conn, notification_id, session["user_id"])
    conn.execute("UPDATE notifications SET is_read = ? WHERE id = ?", (is_read, note["id"]))
    conn.commit()
    return _back_to_list()


@notifications_bp.route("/notifications/read-all", methods=["POST"])
def mark_all_read():
    user_id = session["user_id"]
    conn = _connection()
    count = unread_count(conn, user_id)
    conn.execute(
        "UPDATE notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0", (user_id,)
    )
    conn.commit()
    session["notification_info"] = (
        f"Marked {count} notification{'' if count == 1 else 's'} as read."
        if count else "You're all caught up."
    )
    return _back_to_list()
