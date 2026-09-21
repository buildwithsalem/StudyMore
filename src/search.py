"""Search and filter study groups. Registered in app.py as a blueprint."""

from flask import (
    Blueprint,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from search_query import (
    GROUP_STATUSES,
    MEETING_TYPES,
    SearchValidationError,
    clean_filters,
    connect,
    search_study_groups,
)

search_bp = Blueprint("search", __name__, template_folder="templates")

DEFAULT_DATABASE = "study_more.db"


def get_connection():
    return connect(current_app.config.get("DATABASE", DEFAULT_DATABASE))


def joined_group_ids(conn, user_id):
    """Ids of the groups this user is already in, so the page can show Joined."""
    if not user_id:
        return set()
    rows = conn.execute(
        "SELECT group_id FROM group_memberships WHERE user_id = ?", (user_id,)
    ).fetchall()
    return {row["group_id"] for row in rows}


@search_bp.route("/search")
def search():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login"))

    form = request.args
    conn = get_connection()
    try:
        try:
            filters = clean_filters(form)
            results, notice = search_study_groups(conn, form)
            error = None
        except SearchValidationError as exc:
            # Keep what the user typed so the form can be redrawn with it.
            filters = {
                "term": (form.get("term") or "").strip(),
                "course_code": (form.get("course_code") or "").strip(),
                "section": (form.get("section") or "").strip(),
                "meeting_type": (form.get("meeting_type") or "").strip(),
                "status": (form.get("status") or "").strip().title(),
                "available_only": bool(form.get("available_only")),
                "is_browse": False,
            }
            results, notice, error = [], None, str(exc)

        joined = joined_group_ids(conn, user_id)
    finally:
        conn.close()

    for group in results:
        group["already_joined"] = group["group_id"] in joined

    return render_template(
        "search.html",
        results=results,
        notice=notice,
        error=error,
        filters=filters,
        meeting_types=MEETING_TYPES,
        group_statuses=GROUP_STATUSES,
        user_id=user_id,
    )
