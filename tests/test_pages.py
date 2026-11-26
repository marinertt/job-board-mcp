import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_pages import GUI_DEMO, build_site


class GitHubPagesBuildTests(unittest.TestCase):
    def test_build_uses_static_mode_and_only_includes_demo_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = build_site(Path(temporary) / "site")

            page = (output / "index.html").read_text(encoding="utf-8")
            demo = json.loads((output / "gui-demo.json").read_text(encoding="utf-8"))

            self.assertIn('<body data-mode="static">', page)
            self.assertIn("<title>Job Monitor | SPARKTACUS</title>", page)
            self.assertIn('href="https://www.sparktacus.io/job-monitor/" aria-current="page"', page)
            self.assertIn('id="results-view-tab" class="view-tab" role="tab" data-workspace-tab aria-selected="true"', page)
            self.assertIn('id="how-it-works-view" class="view-panel" role="tabpanel" aria-labelledby="how-it-works-tab" hidden', page)
            self.assertIn("MCP client", page)
            self.assertNotIn('<body data-mode="local">', page)
            self.assertEqual({path.name for path in output.iterdir()}, {"index.html", "gui-demo.json"})
            self.assertTrue(all(".invalid/" in job["url"] for job in demo["fixtures"]))
            self.assertEqual(demo, json.loads(GUI_DEMO.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
