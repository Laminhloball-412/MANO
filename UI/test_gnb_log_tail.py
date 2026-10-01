import os
import tempfile
import unittest

try:
    from gnb_log_tail import read_appended_lines
except ModuleNotFoundError:
    read_appended_lines = None


class GnbLogTailTests(unittest.TestCase):
    def test_reads_only_lines_added_after_the_launch_offset(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as handle:
            handle.write("old gNB session\n")
            path = handle.name
        try:
            self.assertIsNotNone(read_appended_lines)
            offset = os.path.getsize(path)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write("PDU session QoS requested\n")
                handle.write("PDU session QoS accepted\n")

            lines, next_offset = read_appended_lines(path, offset)

            self.assertEqual(
                ["PDU session QoS requested", "PDU session QoS accepted"], lines
            )
            self.assertEqual(os.path.getsize(path), next_offset)
        finally:
            os.unlink(path)

    def test_recovers_when_gnb_truncates_its_log_on_restart(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as handle:
            handle.write("this is an old, larger log file\n")
            path = handle.name
        try:
            self.assertIsNotNone(read_appended_lines)
            old_offset = os.path.getsize(path)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("fresh InitialUEMessage\n")

            lines, next_offset = read_appended_lines(path, old_offset)

            self.assertEqual(["fresh InitialUEMessage"], lines)
            self.assertEqual(os.path.getsize(path), next_offset)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
