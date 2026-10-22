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

PHX-123 (ADR-184): ``--install`` (also when Chromium is already there) records
a SHA-256 manifest of the folder's browser executables in
``%LOCALAPPDATA%\\PHOENIX\\browser-manifest.json``; PHOENIX refuses to launch a
browser that no longer matches it. The check also verifies the manifest (exit 1
when it is missing or does not match). Run ``--install`` only on a folder you
trust: the manifest records what is there now.
"""

import argparse
import subprocess
import sys

from phoenix.tools.browser_setup import (
    BrowserNotTrusted,
    browsers_path,
    chromium_installed,
    install_command,
    manifest_path,
    verify_browsers,
    write_manifest,
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
        manifest = manifest_path()
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"PHOENIX browser folder: {path}")
    print(f"Browser manifest: {manifest}")
    if not options.install:
        if not chromium_installed(path):
            print("Playwright Chromium: not installed (run again with --install)")
            return 1
        try:
            verify_browsers(path, manifest)
        except BrowserNotTrusted as error:
            print(f"Playwright Chromium: installed, not verified ({error})")
            print("Run again with --install to record a folder you trust.")
            return 1
        print("Playwright Chromium: installed and verified")
        return 0
    if not chromium_installed(path):
        command, environment = install_command(path)
        print("Running: " + " ".join(command))
        completed = subprocess.run(command, env=environment, check=False)
        if completed.returncode != 0 or not chromium_installed(path):
            print("Playwright Chromium: install failed", file=sys.stderr)
            return 1
    try:
        count = write_manifest(path, manifest)
    except (BrowserNotTrusted, OSError) as error:
        print(f"Browser manifest: not written ({error})", file=sys.stderr)
        return 1
    print(f"Playwright Chromium: installed; manifest records {count} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
