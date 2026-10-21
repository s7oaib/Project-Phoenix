"""PHX-112 owner-run setup: PHOENIX's own Playwright Chromium (ADR-150).

Explicit setup step only; PHOENIX never downloads a browser at runtime.

    uv run python scripts/install_control_browser.py            # check only
    uv run python scripts/install_control_browser.py --install  # download

The folder is ``D:\\Phoenix\\browsers`` unless ``PHOENIX_BROWSERS_PATH`` names
another absolute path. ``--install`` runs ``python -m playwright install
chromium`` with ``PLAYWRIGHT_BROWSERS_PATH`` set to that folder, so the
download (from Playwright's own CDN) lands there and nowhere else. It does not
touch the owner's Chrome/Edge or their profiles. Exit codes: 0 installed (or
check passed), 1 not installed / install failed, 2 usage error.
"""

import argparse
import subprocess
import sys

from phoenix.tools.browser_setup import (
    browsers_path,
    chromium_installed,
    install_command,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--install",
        action="store_true",
        help="download Playwright's Chromium into PHOENIX's browser folder",
    )
    options = parser.parse_args(argv)
    try:
        path = browsers_path()
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"PHOENIX browser folder: {path}")
    if chromium_installed(path):
        print("Playwright Chromium: installed")
        return 0
    if not options.install:
        print("Playwright Chromium: not installed (run again with --install)")
        return 1
    command, environment = install_command(path)
    print("Running: " + " ".join(command))
    completed = subprocess.run(command, env=environment, check=False)
    if completed.returncode != 0 or not chromium_installed(path):
        print("Playwright Chromium: install failed", file=sys.stderr)
        return 1
    print("Playwright Chromium: installed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
