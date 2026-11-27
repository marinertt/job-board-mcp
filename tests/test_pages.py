import json
import tempfile
import unittest
from pathlib import Path

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
            self.assertIn('href="https://www.sparktacus.io/about/">About</a>', page)
            self.assertIn(
                'id="results-view-tab" class="view-tab" role="tab" data-workspace-tab aria-selected="true"',
                page,
            )
            self.assertIn(
                'id="how-it-works-view" class="view-panel" role="tabpanel" aria-labelledby="how-it-works-tab" hidden',
                page,
            )
            self.assertIn("MCP client", page)
            self.assertIn('id="profiles-panel"', page)
            self.assertNotIn('id="profile-select"', page)
            self.assertIn('id="create-profile"', page)
            self.assertIn('id="add-source-panel"', page)
            self.assertIn('id="resources-panel"', page)
            self.assertIn('id="results-search"', page)
            self.assertIn('id="results-search-panel" class="section source-search"', page)
            self.assertNotIn('id="resource-view-tab"', page)
            self.assertIn('id="active-profile-context"', page)
            self.assertIn('id="resource-select"', page)
            self.assertIn('id="read-resource"', page)
            self.assertIn('id="seed-demo"', page)
            self.assertNotIn("Sample board", page)
            self.assertIn("Save in this browser", page)
            sidebar, main = page.split('<main id="main">', 1)
            self.assertLess(
                sidebar.index('id="profiles-panel"'), sidebar.index('id="resources-panel"')
            )
            self.assertLess(
                sidebar.index('id="resources-panel"'), sidebar.index('id="save-config"')
            )
            self.assertIn('id="resource-select"', sidebar)
            self.assertNotIn('id="resource-select"', main)
            self.assertEqual(main.count('role="tab" data-workspace-tab'), 2)
            self.assertIn("Local fixture search only. No external job source was contacted.", page)
            self.assertNotIn('<body data-mode="local">', page)
            self.assertEqual(
                {path.name for path in output.iterdir()}, {"index.html", "gui-demo.json"}
            )
            self.assertTrue(all(".invalid/" in job["url"] for job in demo["fixtures"]))
            self.assertEqual(len(demo["fixtures"]), 6)
            self.assertEqual(
                {job["region"] for job in demo["fixtures"]},
                {"Example Region", "Fully Remote", "Career Transition Jobs"},
            )
            self.assertEqual(demo, json.loads(GUI_DEMO.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
