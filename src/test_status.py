import unittest
from datetime import datetime, timedelta

from search_query import derive_display_status, meeting_has_passed


def future_date_time():
    dt = datetime.now() + timedelta(days=7)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


def past_date_time():
    dt = datetime.now() - timedelta(days=1)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


class TestMeetingHasPassed(unittest.TestCase):

    def test_future_meeting_has_not_passed(self):
        date, time = future_date_time()
        self.assertFalse(meeting_has_passed(date, time))

    def test_past_meeting_has_passed(self):
        date, time = past_date_time()
        self.assertTrue(meeting_has_passed(date, time))

    def test_bad_input_does_not_crash(self):
        self.assertFalse(meeting_has_passed(None, None))
        self.assertFalse(meeting_has_passed("not-a-date", "not-a-time"))


class TestDeriveDisplayStatus(unittest.TestCase):

    def test_open_group_under_capacity(self):
        date, time = future_date_time()
        result = derive_display_status("open", 2, 6, date, time)
        self.assertEqual(result, "Open")

    def test_open_group_at_capacity_is_full(self):
        date, time = future_date_time()
        result = derive_display_status("open", 6, 6, date, time)
        self.assertEqual(result, "Full")

    def test_creator_marked_completed_stays_completed(self):
        date, time = future_date_time()
        result = derive_display_status("Completed", 2, 6, date, time)
        self.assertEqual(result, "Completed")

    def test_creator_marked_cancelled_stays_cancelled(self):
        date, time = future_date_time()
        result = derive_display_status("Cancelled", 2, 6, date, time)
        self.assertEqual(result, "Cancelled")

    def test_past_meeting_time_becomes_completed(self):
        date, time = past_date_time()
        result = derive_display_status("open", 2, 6, date, time)
        self.assertEqual(result, "Completed")

    def test_past_meeting_time_overrides_full(self):
        date, time = past_date_time()
        result = derive_display_status("open", 6, 6, date, time)
        self.assertEqual(result, "Completed")

    def test_no_meeting_time_given_still_works(self):
        result = derive_display_status("open", 2, 6, None, None)
        self.assertEqual(result, "Open")


if __name__ == "__main__":
    unittest.main()
