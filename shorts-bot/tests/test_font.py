import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from shorts.render import split_runs


class FontRuns(unittest.TestCase):
    def test_mixed_runs(self):
        self.assertEqual(split_runs("AI টুল ও প্রম্পট"),
                         [(False, "AI "), (True, "টুল ও প্রম্পট")])

    def test_handle_and_punctuation(self):
        self.assertEqual(split_runs("@your_channel"), [(False, "@your_channel")])
        self.assertEqual(split_runs("না!"), [(True, "না"), (False, "!")])

    def test_menu_name_inside_bengali(self):
        runs = split_runs("Settings খুলুন")
        self.assertEqual(runs[0], (False, "Settings "))
        self.assertTrue(runs[1][0])


if __name__ == "__main__":
    unittest.main()
