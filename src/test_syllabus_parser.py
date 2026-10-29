import unittest

from syllabus_parser import (balance_parens, parse_date_cell, parse_tables_and_text,
                             split_bullets, split_due, title_case_assignment)

HEADER = ["Class Date(s)", "Class", "Class Activities/Lecture", "Due Dates"]
TEXT = ("CSE 1000 Syllabus Updated: 08/12/2026\nCourse Title: Intro\n"
        "CSE 1000-001\nFr 4:00PM - 6:50PM WH 311\n")


class SyllabusParserTests(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(parse_date_cell("September 04"), (9, 4, None))
        self.assertEqual(parse_date_cell("Oct 9, 2026"), (10, 9, 2026))
        self.assertEqual(parse_date_cell("10/16/26"), (10, 16, 2026))
        self.assertIsNone(parse_date_cell("Week 3"))

    def test_merged_cells_and_columns(self):
        rows = [HEADER,
                ["October 09", None, None, "8", "\u2022 Lecture\n\u2022 Quiz 1", None, None, ""],
                ["October 16", None, None, "9", "\u2022 Exam #1 (Chapters 1-4)", None, None, "Assignment #2"]]
        out = parse_tables_and_text([rows], TEXT, default_year=2026)
        got = {(i["type"], i["date"]) for i in out["items"]}
        self.assertEqual(got, {("Quiz", "2026-10-09"), ("Exam", "2026-10-16"), ("Assignment", "2026-10-16")})

    def test_lectures_are_not_deadlines(self):
        rows = [HEADER, ["August 21", "1", "\u2022 Course Introduction\n\u2022 Lecture - Chapter 1", ""]]
        self.assertEqual(parse_tables_and_text([rows], TEXT, default_year=2026)["items"], [])

    def test_year_rolls_over(self):
        rows = [HEADER, ["December 04", "14", "\u2022 Final Exam", ""], ["January 15", "1", "\u2022 Quiz 1", ""]]
        dates = [i["date"] for i in parse_tables_and_text([rows], TEXT, default_year=2026)["items"]]
        self.assertEqual(dates, ["2026-12-04", "2027-01-15"])

    def test_exam_gets_class_time_for_chosen_section(self):
        rows = [HEADER, ["October 16", "9", "\u2022 Exam #1", ""]]
        out = parse_tables_and_text([rows], TEXT, default_year=2026, section="001")
        self.assertEqual((out["items"][0]["start_time"], out["items"][0]["end_time"]), ("16:00", "18:50"))

    def test_wrapped_due_cell_splits_assignments(self):
        parts = split_due("Assignment #3 \u2013 Video\nSpeeches (Upload to\nCanvas)\nAssignment #5")
        self.assertEqual(parts, ["Assignment #3 \u2013 Video Speeches (Upload to Canvas)", "Assignment #5"])

    def test_titles(self):
        self.assertEqual(balance_parens("Midterm (chapters 1-4"), "Midterm")
        self.assertEqual(title_case_assignment("Exam #1 (Chapters 1-4)"), "Exam #1 (Chapters 1-4)")
        self.assertEqual(title_case_assignment("Assignment #3 \u2013 Video"), "Assignment #3: Video")
        self.assertEqual(split_bullets("\u2022 Lectures \u2013 Chapter 7 \u201cEvaluating\nTechnology\u201d\n\u2022 Quiz 2"),
                         ["Lectures \u2013 Chapter 7 \u201cEvaluating Technology\u201d", "Quiz 2"])

    def test_text_fallback_warns(self):
        out = parse_tables_and_text([], TEXT + "Midterm Exam: Oct 15\nFinal Exam - December 10\n", default_year=2026)
        self.assertEqual([i["date"] for i in out["items"]], ["2026-10-15", "2026-12-10"])
        self.assertTrue(out["warnings"])


if __name__ == "__main__":
    unittest.main()