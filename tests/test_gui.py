import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from job_board_mcp.gui import JobBoardDemo


class FakeBridge:
    instances = []

    def __init__(self, database, profile_path, sources_path):
        self.closed = False
        self.instances.append(self)

    def close(self):
        self.closed = True


class JobBoardDemoTests(unittest.TestCase):
    def setUp(self):
        FakeBridge.instances = []
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def test_creates_private_config_copies_and_reconnects_on_save(self):
        with patch("job_board_mcp.gui.MCPBridge", FakeBridge):
            demo = JobBoardDemo(Path(self.directory.name))
            profile = json.loads(demo.profile_path.read_text(encoding="utf-8"))
            sources = json.loads(demo.sources_path.read_text(encoding="utf-8"))
            profile["discovery_days"] = 5

            demo.save_configuration(profile, sources)

            self.assertTrue(FakeBridge.instances[0].closed)
            self.assertEqual(len(FakeBridge.instances), 2)
            self.assertEqual(
                json.loads(demo.profile_path.read_text(encoding="utf-8"))["discovery_days"],
                5,
            )
            self.assertEqual(demo.database_path.parent, Path(self.directory.name).resolve())

    def test_rejects_invalid_profile_before_replacing_configuration(self):
        with patch("job_board_mcp.gui.MCPBridge", FakeBridge):
            demo = JobBoardDemo(Path(self.directory.name))
            before = demo.profile_path.read_bytes()
            sources = json.loads(demo.sources_path.read_text(encoding="utf-8"))

            with self.assertRaisesRegex(ValueError, "regions array"):
                demo.save_configuration({"discovery_days": 4}, sources)

            self.assertEqual(demo.profile_path.read_bytes(), before)
            self.assertEqual(len(FakeBridge.instances), 1)


if __name__ == "__main__":
    unittest.main()
