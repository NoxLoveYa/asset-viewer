# SPDX-License-Identifier: AGPL-3.0-or-later
import json
import os
import threading
import unittest

import helpers
from viewer.picks import BadPick, CorruptFile, PicksStore, ReviewReader, parse_cut
from viewer.project import Project


class CutTest(unittest.TestCase):
    def test_good_cuts(self):
        self.assertEqual(parse_cut([1.234, 2.5]), [1.23, 2.5])
        self.assertEqual(parse_cut([0, 3600]), [0.0, 3600.0])
        self.assertIsNone(parse_cut(None))

    def test_bad_cuts(self):
        for bad in ([2, 1], [1, 1], [-1, 1], [0, True], [0, float("nan")], [0, float("inf")], [1], [1, 2, 3], "1-2", [0, 3601], {}, [None, 1]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_cut(bad)


class PicksTest(helpers.ProjectCase):
    def setUp(self):
        super().setUp()
        self.project = Project(self.project_dir)
        self.store = PicksStore(self.project, "data/picks.json")
        self.file = os.path.join(self.project_dir, "data", "picks.json")

    def on_disk(self):
        with open(self.file, encoding="utf-8") as f:
            return json.load(f)

    def test_empty_when_no_file(self):
        self.assertEqual(self.store.read()[1], {})

    def test_post_note_pick_and_clear(self):
        rev0, _ = self.store.read()
        rev1, picks = self.store.update({"id": "a.wav", "picked": True, "note": "good"})
        self.assertGreater(rev1, rev0)
        self.assertEqual((picks["a.wav"]["picked"], picks["a.wav"]["note"]), (True, "good"))
        self.assertRegex(picks["a.wav"]["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$")
        self.assertEqual(self.on_disk(), picks)
        rev2, picks = self.store.update({"id": "a.wav", "picked": False, "note": ""})
        self.assertGreater(rev2, rev1)
        self.assertEqual(picks, {})
        self.assertEqual(self.on_disk(), {})

    def test_partial_updates_keep_the_other_fields(self):  # audit H5
        self.store.update({"id": "a", "picked": True, "note": "n", "cut": [1, 2]})
        self.store.update({"id": "a", "note": "n2"})
        self.assertEqual(self.on_disk()["a"], {**self.on_disk()["a"], "picked": True, "note": "n2", "cut": [1.0, 2.0]})
        self.store.update({"id": "a", "picked": False})
        e = self.on_disk()["a"]
        self.assertEqual((e["picked"], e["note"], e["cut"]), (False, "n2", [1.0, 2.0]))

    def test_cut_kept_without_cut_and_cleared_by_null(self):
        self.store.update({"id": "s.wav", "picked": True, "cut": [1.234, 2.5]})
        self.store.update({"id": "s.wav", "picked": True, "note": "x"})
        self.assertEqual(self.on_disk()["s.wav"]["cut"], [1.23, 2.5])
        self.store.update({"id": "s.wav", "cut": None})
        self.assertNotIn("cut", self.on_disk()["s.wav"])
        self.store.update({"id": "s.wav", "picked": False, "note": ""})
        self.assertEqual(self.on_disk(), {})

    def test_a_cut_alone_keeps_the_entry(self):
        self.store.update({"id": "s.wav", "cut": [0, 1]})
        self.assertEqual(self.on_disk()["s.wav"]["cut"], [0.0, 1.0])
        self.assertIs(self.on_disk()["s.wav"]["picked"], False)

    def test_bad_bodies(self):
        bad = [[], "x", {}, {"id": ""}, {"id": 5}, {"id": "x" * 500}, {"id": "a", "picked": "yes"}, {"id": "a", "picked": 1},
               {"id": "a", "note": 5}, {"id": "a", "note": "x" * 2001}, {"id": "a", "cut": [2, 1]}, {"id": "a", "cut": "x"},
               {"id": "a", "other": 1}, {"group": [], "picked": None}, {"group": "ab"}, {"group": ["a", 5]},
               {"group": ["a", "b"], "picked": "c"}, {"group": ["a"], "picked": "a", "x": 1}]
        for body in bad:
            with self.subTest(body=body):
                with self.assertRaises(BadPick):
                    self.store.update(body)
        self.assertFalse(os.path.exists(self.file))

    def test_note_limit_is_inclusive(self):
        self.store.update({"id": "a", "note": "x" * 2000})
        self.assertEqual(len(self.on_disk()["a"]["note"]), 2000)

    def test_group_pick_is_atomic(self):  # audit M5
        self.store.update({"id": "a", "picked": True, "note": "keep my note"})
        self.store.update({"id": "b", "picked": True})
        rev, picks = self.store.update({"group": ["a", "b", "c"], "picked": "c"})
        self.assertEqual(sorted(picks), ["a", "c"])  # b had nothing else to keep
        self.assertEqual((picks["a"]["picked"], picks["a"]["note"], picks["c"]["picked"]), (False, "keep my note", True))
        self.assertEqual(self.on_disk(), picks)
        _, picks = self.store.update({"group": ["a", "b", "c"], "picked": None})
        self.assertEqual(sorted(picks), ["a"])

    def test_corrupt_file_is_never_overwritten(self):  # audit H2
        bad = ["<<<<<<< HEAD\n{}\n=======\n{}\n>>>>>>> x", "[1, 2]", '{"a": 5}', "", "\x00\x01"]
        for text in bad:
            with self.subTest(text=text[:12]):
                self.write("data/picks.json", text)
                with self.assertRaises(CorruptFile):
                    self.store.read()
                with self.assertRaises(CorruptFile):
                    self.store.update({"id": "a", "picked": True})
                with self.assertRaises(CorruptFile):
                    self.store.update({"group": ["a"], "picked": "a"})
                with open(self.file, encoding="utf-8") as f:
                    self.assertEqual(f.read(), text)

    def test_corrupt_message_names_the_file(self):
        self.write("data/picks.json", "{")
        with self.assertRaisesRegex(CorruptFile, "data/picks.json"):
            self.store.read()

    def test_rev_grows_on_every_write_and_on_outside_edits(self):
        r1 = self.store.update({"id": "a", "picked": True})[0]
        r2 = self.store.update({"id": "a", "picked": False, "note": "x"})[0]
        self.assertEqual(self.store.read()[0], r2)
        self.assertGreater(r2, r1)
        self.write("data/picks.json", json.dumps({"z": {"picked": True, "note": "", "at": "x"}}))
        r3, picks = self.store.read()
        self.assertGreater(r3, r2)
        self.assertEqual(list(picks), ["z"])
        self.assertEqual(self.store.read()[0], r3)

    def test_rev_survives_a_restart(self):
        r1 = self.store.update({"id": "a", "picked": True})[0]
        later = PicksStore(self.project, "data/picks.json")
        self.assertGreaterEqual(later.read()[0], r1 - 1000)  # time based start, never near zero

    def test_file_format_is_sorted_json_indent_2(self):
        self.store.update({"id": "b", "picked": True})
        self.store.update({"id": "a", "picked": True})
        with open(self.file, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(list(json.loads(text)), ["a", "b"])
        self.assertIn('\n  "a": {', text)

    def test_concurrent_posts_lose_nothing(self):
        errors = []

        def post(i):
            try:
                self.store.update({"id": "item-%02d" % i, "picked": True, "note": "n%d" % i})
            except Exception as e:  # noqa
                errors.append(e)
        threads = [threading.Thread(target=post, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(self.on_disk()), ["item-%02d" % i for i in range(20)])
        self.assertEqual([n for n in os.listdir(os.path.dirname(self.file)) if n.endswith(".tmp")], [])

    def test_concurrent_reads_during_writes(self):
        self.store.update({"id": "seed", "picked": True})
        errors = []
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    self.store.read()
                except Exception as e:  # noqa
                    errors.append(e)
        t = threading.Thread(target=reader)
        t.start()
        for i in range(30):
            self.store.update({"id": "k%d" % i, "picked": True})
        stop.set()
        t.join()
        self.assertEqual(errors, [])


class ReviewTest(helpers.ProjectCase):
    def reader(self):
        return ReviewReader(Project(self.project_dir), "review.json")

    def test_absent_file_is_no_reviews(self):
        etag, payload = self.reader().read()
        self.assertEqual((payload["reviews"], payload["error"]), ([], None))

    def test_valid_file(self):
        review = {"reviews": [{"title": "T", "groups": [{"id": "g", "items": [{"id": "a.wav", "ref": True}, {"id": "b.wav"}]}]}]}
        self.write("review.json", review)
        _, payload = self.reader().read()
        self.assertEqual(payload["reviews"], review["reviews"])
        self.assertIsNone(payload["error"])

    def test_invalid_files_report_a_path(self):
        cases = [("{", "not valid JSON"), ("[]", "reviews: array expected"), ('{"reviews": [1]}', r"reviews\[0\]: object"),
                 ('{"reviews": [{"title": 1, "groups": []}]}', r"reviews\[0\]\.title: string"),
                 ('{"reviews": [{"title": "t", "groups": [{"id": "g"}]}]}', r"groups\[0\]\.items: array"),
                 ('{"reviews": [{"title": "t", "groups": [{"id": "g", "items": [{"id": "a", "ref": 1}]}]}]}', r"items\[0\]\.ref: boolean")]
        for text, pattern in cases:
            with self.subTest(text=text):
                self.write("review.json", text)
                _, payload = self.reader().read()
                self.assertEqual(payload["reviews"], [])
                self.assertRegex(payload["error"], pattern)

    def test_etag_and_rev_follow_content(self):
        r = self.reader()
        self.write("review.json", {"reviews": []})
        e1, p1 = r.read()
        e1b, p1b = r.read()
        self.assertEqual((e1, p1["rev"]), (e1b, p1b["rev"]))
        self.write("review.json", {"reviews": [{"title": "x", "groups": []}]})
        e2, p2 = r.read()
        self.assertNotEqual(e1, e2)
        self.assertGreater(p2["rev"], p1["rev"])


if __name__ == "__main__":
    unittest.main()
