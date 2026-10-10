"""
Smart Study Matching.

This is the matching logic that used to live inline in the /study-matches route
in app.py, moved here unchanged so the route and the dashboard share one
implementation. Scoring rules are the same as before.
"""


def get_user_study_request(conn, request_id, user_id):
    """One study request, only if it belongs to user_id. Otherwise None."""
    return conn.execute(
        """
        SELECT *
        FROM study_requests
        WHERE id = ? AND user_id = ?
        """,
        (request_id, user_id)
    ).fetchone()


def _availability_set(conn, user_id):
    rows = conn.execute(
        """
        SELECT day_of_week, start_time, end_time
        FROM user_availability
        WHERE user_id = ?
        """,
        (user_id,)
    ).fetchall()
    return {
        (row["day_of_week"], row["start_time"], row["end_time"])
        for row in rows
    }


def find_partner_matches(conn, user_id, study_request):
    """
    Other students with an active request for the same course, best first.
    Only compare active requests for the same course.
    """
    my_availability = _availability_set(conn, user_id)

    candidate_requests = conn.execute(
        """
        SELECT
            sr.*,
            u.name
        FROM study_requests sr
        JOIN users u ON u.id = sr.user_id
        WHERE sr.user_id != ?
          AND sr.status = 'active'
          AND UPPER(sr.course_code) = UPPER(?)
        """,
        (user_id, study_request["course_code"])
    ).fetchall()

    partner_matches = []

    for candidate in candidate_requests:
        reasons = []
        score = 10

        # Same course is required, so every candidate here gets
        # the 10-point course base score.
        reasons.append("Same course")

        if candidate["study_goal"] == study_request["study_goal"]:
            score += 30
            reasons.append("Same study goal")

        if (
            candidate["meeting_preference"]
            == study_request["meeting_preference"]
        ):
            score += 20
            reasons.append("Same meeting preference")

        if (
            study_request["section"]
            and candidate["section"]
            and candidate["section"].strip().lower()
            == study_request["section"].strip().lower()
        ):
            score += 10
            reasons.append("Same section")

        overlapping_slots = (
            my_availability & _availability_set(conn, candidate["user_id"])
        )

        overlap_count = len(overlapping_slots)

        # Availability is worth up to 30 points.
        if overlap_count >= 3:
            score += 30
            reasons.append("Strong availability overlap")
        elif overlap_count == 2:
            score += 20
            reasons.append("2 overlapping time blocks")
        elif overlap_count == 1:
            score += 10
            reasons.append("1 overlapping time block")

        partner_matches.append({
            "request_id": candidate["id"],
            "user_id": candidate["user_id"],
            "name": candidate["name"],
            "course_code": candidate["course_code"],
            "study_goal": candidate["study_goal"],
            "score": score,
            "reasons": reasons,
            "overlap_count": overlap_count,
            "overlapping_slots": sorted(overlapping_slots)
        })

    # Highest compatibility appears first.
    partner_matches.sort(
        key=lambda match: (
            match["score"],
            match["overlap_count"]
        ),
        reverse=True
    )
    return partner_matches


def find_group_matches(conn, study_request):
    """Open groups with room left for the request's course, best first."""
    group_rows = conn.execute(
        """
        SELECT
            sg.*,
            COUNT(gm.id) AS member_count
        FROM study_groups sg
        LEFT JOIN group_memberships gm
            ON gm.group_id = sg.id
        WHERE LOWER(sg.status) = 'open'
          AND UPPER(sg.course_code) = UPPER(?)
        GROUP BY sg.id
        HAVING COUNT(gm.id) < sg.max_members
        """,
        (study_request["course_code"],)
    ).fetchall()

    group_matches = []

    for group in group_rows:
        reasons = ["Same course"]
        score = 40

        if (
            study_request["section"]
            and group["section"]
            and group["section"].strip().lower()
            == study_request["section"].strip().lower()
        ):
            score += 20
            reasons.append("Same section")

        if (
            group["meeting_type"]
            == study_request["meeting_preference"]
        ):
            score += 20
            reasons.append("Same meeting preference")

        # study_goal was added to study_groups for Iteration 2.
        # Older groups may have NULL because they existed before
        # this feature was introduced.
        if (
            group["study_goal"]
            and group["study_goal"]
            == study_request["study_goal"]
        ):
            score += 20
            reasons.append("Same study goal")

        group_matches.append({
            "id": group["id"],
            "group_name": group["group_name"],
            "course_code": group["course_code"],
            "score": score,
            "reasons": reasons
        })

    group_matches.sort(
        key=lambda match: match["score"],
        reverse=True
    )
    return group_matches
