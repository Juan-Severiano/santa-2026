import unittest

from santa2026.core import Puzzle
from santa2026.native_output import read_program_output


class NativeOutputTests(unittest.TestCase):
    def setUp(self):
        self.puzzle = Puzzle("an id with spaces", (3,), (1,), 3)
        self.program = [["/", 0, 0, 1]]
        self.response = 'puzzle [["/",0,0,1]]\n'

    def test_normal_response_accepts_text_bytes_and_no_final_newline(self):
        for output in (self.response, self.response.encode(), self.response.rstrip("\n")):
            with self.subTest(output=output):
                self.assertEqual(read_program_output(output, self.puzzle), self.program)

    def test_normal_response_retains_whitespace_and_formatted_json_semantics(self):
        output = ' \npuzzle [\n ["/",0,0,1]\n]\n '
        self.assertEqual(read_program_output(output, self.puzzle), self.program)

    def test_recovery_ignores_partial_line_and_incomplete_utf8_suffix(self):
        for suffix in ('puzzle [["+', b'puzzle [\xf0\x9f'):
            output = self.response + suffix if isinstance(suffix, str) else self.response.encode() + suffix
            with self.subTest(output=output):
                self.assertEqual(read_program_output(output, self.puzzle, require_complete=True), self.program)

    def test_recovery_requires_a_complete_newline_ended_response(self):
        for output in (None, "", b"", " \n", self.response.rstrip("\n"), b"\xf0\x9f"):
            with self.subTest(output=output):
                self.assertIsNone(read_program_output(output, self.puzzle, require_complete=True))

    def test_recovery_accepts_crlf_and_blank_lines(self):
        output = '\n\r\npuzzle [["/",0,0,1]]\r\n\n'
        self.assertEqual(read_program_output(output, self.puzzle, require_complete=True), self.program)

    def test_complete_malformed_or_duplicate_responses_are_rejected(self):
        for output in ('other [["/",0,0,1]]\n', 'puzzle [\n', 'puzzle []\n',
                       self.response * 2, self.response + "debug log\n", b"puzzle [\xff]\n"):
            with self.subTest(output=output):
                with self.assertRaises(ValueError):
                    read_program_output(output, self.puzzle, require_complete=True)

    def test_empty_valid_program_is_distinct_from_no_response(self):
        covered = Puzzle("covered", (3,), (3,), 3)
        self.assertEqual(read_program_output("puzzle []\n", covered, require_complete=True), [])
        self.assertIsNone(read_program_output("", covered))


if __name__ == "__main__":
    unittest.main()
