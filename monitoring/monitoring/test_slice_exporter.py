import importlib.util
from pathlib import Path
import unittest


def load_exporter_module():
    source = Path(__file__).with_name("06-exporter-app.py")
    spec = importlib.util.spec_from_file_location("slice_exporter", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EmptyConfigurationTest(unittest.TestCase):
    def test_collect_all_accepts_no_slices(self):
        exporter = load_exporter_module()

        exporter.collect_all([])


if __name__ == "__main__":
    unittest.main()
