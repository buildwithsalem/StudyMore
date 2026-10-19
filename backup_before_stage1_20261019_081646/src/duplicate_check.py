"""
Duplicate Group Prevention. Flask side of duplicate_query.py.

This file does not change create_group() in app.py. It runs just before
create_group() handles a submitted form:

    Exact duplicate (same student, course, name, date and time)
        -> blocked, with a link to the group they already made
    Similar open groups in the same course
        -> warning page: view or join one of them, or create anyway
    Nothing similar, or the student already chose "create anyway"
        -> create_group() runs exactly as before

To turn it on, app.py needs two lines next to the search blueprint:

    from duplicate_check import duplicate_bp
    app.register_blueprint(duplicate_bp)
"""

import sqlite3

from flask import Blueprint, current_app, render_template, request, session

from duplicate_query import (
    find_exact_duplicate,
    find_similar_groups,
    has_required_fields,
    proposal_from_form,
)

duplicate_bp = Blueprint("duplicate_check", __name__, template_folder="templates")

DEFAULT_DATABASE = "study_more.db"
CREATE_GROUP_ENDPOINT = "create_group"
CONFIRM_FIELD = "confirm_duplicate"


def get_connection():
    conn = sqlite3.connect(current_app.config.get("DATABASE", DEFAULT_DATABASE))
    conn.row_factory = sqlite3.Row
    return conn


def fields_to_carry_over(form):
    """Every submitted field, so "Create anyway" resubmits exactly what was typed.

    This keeps the hidden Create Group Together fields (partner_id,
    request_id, partner_request_id) so a matched pair is still added.
    """
    return [
        (name, value)
        for name, value in form.items(multi=True)
        if name != CONFIRM_FIELD
    ]


@duplicate_bp.before_app_request
def check_for_duplicate_group():
    """Runs before every request, but only acts on a submitted Create Group form."""
    if request.method != "POST" or request.endpoint != CREATE_GROUP_ENDPOINT:
        return None

    user_id = session.get("user_id")
    if user_id is None:
        # create_group() sends logged-out users to the login page.
        return None

    if not has_required_fields(request.form):
        # Let create_group() report the missing field in its own words.
        return None

    proposal = proposal_from_form(request.form)

    conn = get_connection()
    try:
        existing = find_exact_duplicate(conn, user_id, proposal)
        if existing is not None:
            return render_template(
                "duplicate_warning.html",
                proposal=proposal,
                existing=existing,
                similar_groups=[],
                carried_fields=[],
            ), 409

        if request.form.get(CONFIRM_FIELD) == "yes":
            return None

        similar_groups = find_similar_groups(conn, proposal, user_id)
    finally:
        conn.close()

    if not similar_groups:
        return None

    return render_template(
        "duplicate_warning.html",
        proposal=proposal,
        existing=None,
        similar_groups=similar_groups,
        carried_fields=fields_to_carry_over(request.form),
        confirm_field=CONFIRM_FIELD,
    )
