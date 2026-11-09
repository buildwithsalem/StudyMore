import unittest

from account_deletion import USER_COLS, _columns, _tables, delete_user_account
from test_private_groups import PrivateGroupBase


class AccountDeletionTests(PrivateGroupBase):
    # users: 1 creator, 2 outsider, 3 invited, 4 member
    # group 1 (members 1, 4; pending invite 1 -> 3), group 2 (member 1 only)

    def delete(self, uid):
        conn = self.db()
        delete_user_account(conn, uid)
        conn.commit()
        conn.close()

    def q(self, sql, *args):
        conn = self.db()
        rows = conn.execute(sql, args).fetchall()
        conn.close()
        return rows

    def leftovers(self, uid):
        conn = self.db()
        found = []
        for table in _tables(conn):
            if table == "study_groups":
                continue
            for col in _columns(conn, table):
                if col in USER_COLS:
                    n = conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (uid,)
                    ).fetchone()[0]
                    if n:
                        found.append((table, col, n))
        conn.close()
        return found

    def test_deleting_a_member_removes_account_and_membership_only(self):
        self.delete(4)
        self.assertEqual(self.q("SELECT 1 FROM users WHERE id=4"), [])
        self.assertEqual(self.q("SELECT 1 FROM group_memberships WHERE user_id=4"), [])
        self.assertEqual(len(self.q("SELECT 1 FROM study_groups WHERE id=1")), 1)
        self.assertEqual(len(self.q("SELECT 1 FROM users WHERE id IN (1,2,3)")), 3)

    def test_creator_deletion_hands_group_to_longest_standing_member(self):
        self.delete(1)
        row = self.q("SELECT creator_user_id FROM study_groups WHERE id=1")
        self.assertEqual(row[0][0], 4)
        self.assertEqual(self.leftovers(1), [])

    def test_creator_deletion_removes_group_with_no_other_members(self):
        self.delete(1)
        self.assertEqual(self.q("SELECT 1 FROM study_groups WHERE id=2"), [])
        self.assertEqual(self.q("SELECT 1 FROM group_memberships WHERE group_id=2"), [])

    def test_invitations_sent_by_deleted_user_are_removed(self):
        self.delete(1)
        self.assertEqual(self.q("SELECT 1 FROM group_invitations"), [])

    def test_deleting_invited_student_removes_their_invitation(self):
        self.delete(3)
        self.assertEqual(self.q("SELECT 1 FROM group_invitations WHERE invitee_id=3"), [])
        self.assertEqual(len(self.q("SELECT 1 FROM study_groups")), 2)

    def test_deleting_unknown_user_is_harmless(self):
        self.delete(999)
        self.assertEqual(len(self.q("SELECT 1 FROM users")), 4)


if __name__ == "__main__":
    unittest.main()