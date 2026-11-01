import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import discussion

import app as app_module


class DiscussionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, 'test.db')
        self.old = app_module.app.config['DATABASE']
        app_module.app.config['DATABASE'] = self.path
        app_module.init_db()
        self.conn = sqlite3.connect(self.path)
        self.conn.executemany("INSERT INTO users(id,name,email,password) VALUES (?, 'User', ?, 'x')",
                              [(1, 'one'), (2, 'two'), (3, 'three')])
        for gid in (1, 2):
            self.conn.execute("""INSERT INTO study_groups
                (id,group_name,course_code,description,meeting_type,meeting_date,meeting_time,max_members)
                VALUES (?, 'Group', 'CSE', 'desc', 'Online', '2030-01-01', '12:00', 5)""", (gid,))
        self.conn.executemany('INSERT INTO group_memberships(user_id,group_id) VALUES (?,?)',
                              [(1, 1), (2, 1), (3, 2)])
        self.conn.commit()
        self.client = app_module.app.test_client()
        self.login(1)
        self.base = '/group/1/discussions'

    def tearDown(self):
        self.conn.close()
        app_module.app.config['DATABASE'] = self.old
        self.temp.cleanup()

    def login(self, uid):
        with self.client.session_transaction() as session:
            session.clear()
            if uid is not None:
                session['user_id'] = uid

    def test_create_read_reply_and_order(self):
        for content in ('  first  ', "second ' ; --"):
            response = self.client.post(self.base, data={'content': content, 'author_id': 3})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(self.client.get(response.location).status_code, 200)
        self.conn.execute("UPDATE discussion_posts SET created_at='2026-01-01 00:00:00'")
        self.conn.commit()
        html = self.client.get(self.base).get_data(as_text=True)
        self.assertLess(html.index('id="post-2"'), html.index('id="post-1"'))
        self.conn.row_factory = sqlite3.Row
        posts = self.conn.execute('SELECT * FROM discussion_posts ORDER BY created_at DESC, id DESC').fetchall()
        self.assertEqual([p['id'] for p in posts], [2, 1])
        self.assertEqual(posts[1]['content'], 'first')
        self.assertEqual(posts[0]['author_id'], 1)
        self.login(2)
        for content in ('reply one', 'reply two'):
            self.assertEqual(self.client.post(self.base + '/1/replies', data={'content': content}).status_code, 302)
        self.conn.execute("UPDATE discussion_replies SET created_at='2026-01-01 00:00:00'")
        self.conn.commit()
        html = self.client.get(self.base + '/1').get_data(as_text=True)
        self.assertLess(html.index('id="reply-1"'), html.index('id="reply-2"'))
        replies = self.conn.execute('SELECT * FROM discussion_replies ORDER BY created_at, id').fetchall()
        self.assertEqual([r['id'] for r in replies], [1, 2])
        self.assertEqual(replies[0]['author_id'], 2)
        self.assertEqual(self.client.get('/group/2/discussions').status_code, 403)

    def test_permissions_and_authentication(self):
        self.client.post(self.base, data={'content': 'private'})
        for uid, expected in ((3, 403), (None, 302), (999, 403)):
            self.login(uid)
            for method, path in (('get', self.base), ('post', self.base),
                                 ('get', self.base + '/1'), ('post', self.base + '/1/replies')):
                self.assertEqual(getattr(self.client, method)(path).status_code, expected)
        self.login(1)
        self.conn.execute('DELETE FROM group_memberships WHERE user_id=1')
        self.conn.commit()
        self.assertEqual(self.client.get(self.base + '/1').status_code, 403)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM discussion_posts').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM discussion_replies').fetchone()[0], 0)

    def test_validation_and_limits(self):
        self.client.post(self.base, data={'content': 'x' * 5000})
        for path in (self.base, self.base + '/1/replies'):
            for data in ({}, {'content': ''}, {'content': ' \t\n\u2003'}, {'content': 'x' * 5001}):
                self.assertEqual(self.client.post(path, data=data).status_code, 400)
        self.assertEqual(self.client.post(self.base + '/1/replies', data={'content': 'x' * 5000}).status_code, 302)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM discussion_posts').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM discussion_replies').fetchone()[0], 1)

    def test_missing_resources_and_methods(self):
        self.client.post(self.base, data={'content': 'private'})
        for path in ('/group/999/discussions', self.base + '/999', self.base + '/999/replies'):
            method = self.client.post if path.endswith('replies') else self.client.get
            self.assertEqual(method(path).status_code, 404)
        self.login(3)
        for suffix in ('', '/replies'):
            method = self.client.post if suffix else self.client.get
            self.assertEqual(method('/group/2/discussions/1' + suffix).status_code, 404)
        self.assertEqual(self.client.get(self.base + '/1/replies').status_code, 405)
        self.assertEqual(self.client.delete(self.base).status_code, 405)

    def test_existing_database_upgrade_is_idempotent(self):
        self.conn.execute('DROP TABLE discussion_replies')
        self.conn.execute('DROP TABLE discussion_posts')
        self.conn.commit()
        app_module.init_db(self.path)
        self.client.post(self.base, data={'content': 'persist'})
        app_module.init_db(self.path)
        self.assertIn('persist', self.client.get(self.base).get_data(as_text=True))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM users').fetchone()[0], 3)
        self.assertEqual(len(self.conn.execute('PRAGMA foreign_key_list(discussion_posts)').fetchall()), 2)
        self.assertEqual(len(self.conn.execute('PRAGMA foreign_key_list(discussion_replies)').fetchall()), 2)

    def test_pages_empty_states_metadata_and_escaping(self):
        html = self.client.get(self.base).get_data(as_text=True)
        self.assertIn('No discussions yet', html)
        self.assertIn('name="content"', html)
        self.assertIn('Back to group', html)
        self.client.post(self.base, data={'content': '<script>alert(1)</script>'})
        html = self.client.get(self.base).get_data(as_text=True)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('0 replies', html)
        self.assertIn('User', html)
        self.assertIn('UTC</time>', html)
        self.assertIn('No replies yet', self.client.get(self.base + '/1').get_data(as_text=True))
        self.client.post(self.base + '/1/replies', data={'content': 'answer'})
        self.assertIn('1 reply', self.client.get(self.base).get_data(as_text=True))
        for path in (self.base, self.base + '/1/replies'):
            response = self.client.post(path, data={'content': 'z' * 5001})
            self.assertEqual(response.status_code, 400)
            self.assertIn('z' * 5001, response.get_data(as_text=True))
            self.assertIn('aria-invalid="true"', response.get_data(as_text=True))

    def test_group_link_members_only(self):
        self.assertIn('Open Discussion Board', self.client.get('/group/1').get_data(as_text=True))
        self.login(3)
        self.assertNotIn('Open Discussion Board', self.client.get('/group/1').get_data(as_text=True))

    def test_discussion_connection_enforces_foreign_keys(self):
        from discussion import _connection
        with app_module.app.app_context():
            conn = _connection()
            self.assertEqual(conn.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            for sql in (
                "INSERT INTO discussion_posts(group_id,author_id,content) VALUES (999,1,'x')",
                "INSERT INTO discussion_posts(group_id,author_id,content) VALUES (1,999,'x')",
                "INSERT INTO discussion_replies(post_id,author_id,content) VALUES (999,1,'x')",
            ):
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(sql)
                conn.rollback()

    def test_reply_notification_metadata_and_distinct_replies(self):
        # Make post and reply IDs differ to catch incorrect related_id usage.
        self.client.post(self.base, data={'content': 'first'})
        self.client.post(self.base, data={'content': 'second'})
        self.login(2)
        self.conn.execute("UPDATE users SET name='Reply Author' WHERE id=2")
        self.conn.commit()
        for _ in range(2):
            self.assertEqual(self.client.post(self.base + '/2/replies', data={'content': 'answer'}).status_code, 302)
        rows = self.conn.execute('SELECT user_id,kind,group_id,related_id,link_url,message,is_read FROM notifications ORDER BY id').fetchall()
        self.assertEqual(len(rows), 2)
        for index, row in enumerate(rows, 1):
            self.assertEqual(row, (1, 'discussion_reply', 1, index, self.base + '/2',
                                  'Reply Author replied to your discussion in Group.', 0))
        self.assertEqual(self.client.get(rows[0][4]).status_code, 200)

    def test_self_reply_does_not_notify(self):
        self.client.post(self.base, data={'content': 'post'})
        self.client.post(self.base + '/1/replies', data={'content': 'my reply'})
        self.assertEqual(self.conn.execute('SELECT count(*) FROM notifications').fetchone()[0], 0)

    def test_rejected_requests_do_not_notify(self):
        self.client.post(self.base, data={'content': 'post'})
        cases = [(None, self.base + '/1/replies', 'valid', 302),
                 (3, self.base + '/1/replies', 'valid', 403),
                 (3, '/group/2/discussions/1/replies', 'valid', 404),
                 (2, self.base + '/999/replies', 'valid', 404),
                 (2, '/group/999/discussions/1/replies', 'valid', 404)]
        cases += [(2, self.base + '/1/replies', value, 400) for value in ('', ' \t\n', 'x' * 5001)]
        for uid, path, content, status in cases:
            self.login(uid)
            self.assertEqual(self.client.post(path, data={'content': content}).status_code, status)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM notifications').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM discussion_replies').fetchone()[0], 0)

    def test_notification_failure_rolls_back_both_inserts(self):
        self.client.post(self.base, data={'content': 'post'})
        self.login(2)
        original = discussion.create_notification
        def fail_after_insert(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('notification failure')
        with patch.object(discussion, 'create_notification', side_effect=fail_after_insert):
            with patch.dict(app_module.app.config, TESTING=True):
                with self.assertRaisesRegex(RuntimeError, 'notification failure'):
                    self.client.post(self.base + '/1/replies', data={'content': 'answer'})
        self.assertEqual(self.conn.execute('SELECT count(*) FROM discussion_replies').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM notifications').fetchone()[0], 0)

    def test_creator_member_nonmember_controls(self):
        self.conn.execute('UPDATE study_groups SET creator_user_id=1 WHERE id=1')
        self.conn.commit()
        for uid in (1, 2, 3):
            self.login(uid)
            html = self.client.get('/group/1').get_data(as_text=True)
            self.assertIn('group-action-row', html)
            self.assertEqual('Open Discussion Board' in html, uid in (1, 2))
            self.assertEqual('Edit Details' in html, uid == 1)
            self.assertEqual('Members &amp; Invitations' in html, uid == 1)
            self.assertEqual('group-membership-row' in html, uid == 2)
            self.assertEqual('Leave Group' in html, uid == 2)
            self.assertEqual('Join Group' in html, uid == 3)

    def test_reply_html_is_escaped(self):
        self.client.post(self.base, data={'content': 'post'})
        self.login(2)
        self.client.post(self.base + '/1/replies', data={'content': '<script>evil()</script>'})
        html = self.client.get(self.base + '/1').get_data(as_text=True)
        self.assertIn('&lt;script&gt;evil()', html)
        self.assertNotIn('<script>evil()', html)

    def test_replies_survive_reinitialization(self):
        self.client.post(self.base, data={'content': 'post'})
        self.login(2)
        self.client.post(self.base + '/1/replies', data={'content': 'persisted reply'})
        app_module.init_db(self.path)
        self.assertIn('persisted reply', self.client.get(self.base + '/1').get_data(as_text=True))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM notifications').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
