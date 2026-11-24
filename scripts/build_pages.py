"""Build the static, simulated GitHub Pages demo."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUI_HTML = ROOT / "job_board_mcp" / "data" / "gui.html"
GUI_DEMO = ROOT / "job_board_mcp" / "data" / "gui-demo.json"
LOCAL_MODE_MARKER = '<body data-mode="local">'
PAGES_MODE_MARKER = '<body data-mode="static">'
PAGE_TITLE = "<title>Job Monitor | SPARKTACUS</title>"
PAGES_METADATA = (
    '<meta name="description" content="Explore a simulated job-monitor MCP workbench on the SPARKTACUS portfolio.">'
    '<link rel="canonical" href="https://www.sparktacus.io/job-monitor/">'
)


def build_site(output: Path) -> Path:
    html = GUI_HTML.read_text(encoding="utf-8")
    if html.count(LOCAL_MODE_MARKER) != 1:
        raise ValueError("GUI HTML must contain exactly one local-mode marker.")
    demo = json.loads(GUI_DEMO.read_text(encoding="utf-8"))
    if not isinstance(demo.get("fixtures"), list) or not isinstance(demo.get("profile"), dict):
        raise ValueError("GUI demo data must contain a profile and fixture list.")
    if html.count("</head>") != 1 or html.count("<title>") != 1:
        raise ValueError("GUI HTML must have one title and one closing head tag.")
    html = html.replace(
        "<title>Job Board MCP · interactive workbench</title>",
        PAGE_TITLE,
        1,
    )
    html = html.replace("</head>", f"{PAGES_METADATA}</head>", 1)
    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text(
        html.replace(LOCAL_MODE_MARKER, PAGES_MODE_MARKER, 1),
        encoding="utf-8",
    )
    (output / "gui-demo.json").write_text(
        json.dumps(demo, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "pages")
    args = parser.parse_args()
    build_site(args.output.resolve())
    print(f"Built GitHub Pages demo in {args.output.resolve()}")


if __name__ == "__main__":
    main()
