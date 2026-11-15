"""Full self-service account deletion.

Discovers every table that points at a user (declared foreign keys or the
common column names below), so tables added later are cleaned up too.
"""

USER_COLS = {
    "user_id", "author_id", "inviter_id", "invitee_id",
    "created_by_user_id", "requester_id", "sender_id", "recipient_id",
    "friend_id", "addressee_id",
}

from db import table_names, column_names, foreign_keys, identifier


def _tables(conn):
    return table_names(conn)


def _columns(conn, table):
    return column_names(conn, table)


def _fk_children(conn, parent):
    """(child_table, child_column) pairs that reference parent.id."""
    out = set()
    for table in _tables(conn):
        for parent_table, column in foreign_keys(conn, table):
            if parent_table == parent:
                out.add((table, column))
    if parent == "study_groups":
        for table in _tables(conn):
            if "group_id" in _columns(conn, table):
                out.add((table, "group_id"))
    return out


def _purge(conn, table, column, values, seen):
    """Delete rows where column IN values, children first."""
    values = tuple(values)
    key = (table, column, values)
    if not values or key in seen:
        return
    seen.add(key)
    marks = ",".join("?" * len(values))
    if "id" in _columns(conn, table):
        ids = [r[0] for r in conn.execute(
            f"SELECT id FROM {identifier(table)} WHERE {identifier(column)} IN ({marks})", values
        ).fetchall()]
        if ids:
            for child, col in _fk_children(conn, table):
                if (child, col) != (table, column):
                    _purge(conn, child, col, ids, seen)
    conn.execute(f"DELETE FROM {identifier(table)} WHERE {identifier(column)} IN ({marks})", values)


def delete_user_account(conn, user_id):
    """Remove the user and everything of theirs. Caller commits."""
    seen = set()

    # 1. Groups they created: hand to longest-standing member, or delete if empty.
    for (group_id,) in conn.execute(
        "SELECT id FROM study_groups WHERE creator_user_id = ?", (user_id,)
    ).fetchall():
        heir = conn.execute(
            "SELECT user_id FROM group_memberships "
            "WHERE group_id = ? AND user_id != ? ORDER BY id LIMIT 1",
            (group_id, user_id),
        ).fetchone()
        if heir:
            conn.execute(
                "UPDATE study_groups SET creator_user_id = ? WHERE id = ?",
                (heir[0], group_id),
            )
        else:
            _purge(conn, "study_groups", "id", [group_id], seen)

    # 2. Their rows in every table that points at a user.
    fk_user_cols = set()
    for table in _tables(conn):
        for parent_table, column in foreign_keys(conn, table):
            if parent_table == "users":
                fk_user_cols.add((table, column))
    for table in _tables(conn):
        if table == "users":
            continue
        for col in _columns(conn, table):
            if col in USER_COLS or (table, col) in fk_user_cols:
                _purge(conn, table, col, [user_id], seen)

    # 3. The account itself.
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
