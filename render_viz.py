#!/usr/bin/env python
"""
Render a Plotly figure returned by the /api/query endpoint.

Usage:
  python render_viz.py --input resp.json --output viz.html

The input JSON should be the raw API response containing a top-level "figure".
"""

import argparse
import json
from pathlib import Path

import plotly.io as pio


def main() -> None:
    parser = argparse.ArgumentParser(description="Render Plotly figure from API response.")
    parser.add_argument(
        "--input",
        "-i",
        type=Path,
        default=Path("resp.json"),
        help="Path to the API response JSON file (default: resp.json).",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("viz.html"),
        help="Path to write the rendered HTML file (default: viz.html).",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the HTML file in a browser after rendering.",
    )
    args = parser.parse_args()

    if not args.input.exists():
        raise FileNotFoundError(f"Input file not found: {args.input}")

    with args.input.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if "figure" not in data or data["figure"] is None:
        raise ValueError("Input JSON must contain a top-level 'figure' object.")

    fig = pio.from_json(json.dumps(data["figure"]))
    pio.write_html(fig, file=str(args.output), auto_open=args.open)
    print(f"Wrote visualization to {args.output}")


if __name__ == "__main__":
    main()
