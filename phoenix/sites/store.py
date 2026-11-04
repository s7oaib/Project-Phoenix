# ruff: noqa: E501
"""On-disk sites: <root>/<slug>/versions/v0001.json, meta.json {head, max},
site/index.html (render of head) and site/assets/. Undo/redo move ``head``; a
new version after an undo drops the redo tail.

``root`` is <workspace_root>/outputs/sites. The slug is the only caller-chosen
path part and must match SLUG_RE, so nothing can climb out of ``root``.
"""

import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from phoenix.sites.render import data_uri, render
from phoenix.sites.spec import SLUG_RE, SiteError, SiteSpec, validate_spec

MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")
# Parts that are never readable as a site image, below whichever root allows it.
DENIED_PARTS = {
    "appdata",
    "windows",
    "program files",
    "program files (x86)",
    ".ssh",
    ".gnupg",
    "user data",
    "keychains",
    "credentials",
}


def slugify(text: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-")
    return words if SLUG_RE.fullmatch(words) else "site"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class SiteStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def dir(self, slug: str) -> Path:
        if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
            raise SiteError("invalid site id")
        path = (self.root / slug).resolve()
        if path.parent != self.root.resolve():
            raise SiteError("invalid site id")
        return path

    def exists(self, slug: str) -> bool:
        try:
            return (self.dir(slug) / "meta.json").is_file()
        except SiteError:
            return False

    def unique_slug(self, base: str) -> str:
        base = slugify(base)
        slug, n = base, 2
        while (self.root / slug).exists():
            slug = f"{base[: 40 - len(str(n)) - 1]}-{n}"
            n += 1
        return slug

    def meta(self, slug: str) -> dict[str, Any]:
        try:
            meta = json.loads(
                (self.dir(slug) / "meta.json").read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            raise SiteError("site not found") from None
        return {
            "head": int(meta["head"]),
            "max": int(meta["max"]),
            "updated_at": str(meta.get("updated_at", "")),
        }

    def load(self, slug: str, version: int | None = None) -> SiteSpec:
        meta = self.meta(slug)
        n = meta["head"] if version is None else version
        raw = (self.dir(slug) / "versions" / f"v{n:04d}.json").read_text(
            encoding="utf-8"
        )
        return validate_spec(json.loads(raw))

    def summary(self, slug: str) -> dict[str, Any]:
        meta, spec = self.meta(slug), self.load(slug)
        return {"id": slug, "name": spec.name, "type": spec.type, **meta}

    def list_sites(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        out = []
        for child in self.root.iterdir():
            try:
                if child.is_dir() and self.exists(child.name):
                    out.append(self.summary(child.name))
            except (SiteError, ValueError, OSError, KeyError):
                continue  # a damaged site must not hide the others
        return sorted(out, key=lambda s: s["updated_at"], reverse=True)

    def _commit(self, slug: str, spec: SiteSpec, head: int, top: int) -> None:
        """Write version ``head`` (dropping anything above it), meta and render."""
        d = self.dir(slug)
        _write(
            d / "versions" / f"v{head:04d}.json",
            json.dumps(spec.model_dump(mode="json"), indent=1),
        )
        for n in range(head + 1, top + 1):
            (d / "versions" / f"v{n:04d}.json").unlink(missing_ok=True)
        self._set_head(slug, head, head)

    def _set_head(self, slug: str, head: int, top: int) -> None:
        d = self.dir(slug)
        _write(
            d / "meta.json",
            json.dumps({"head": head, "max": top, "updated_at": _now()}),
        )
        self.write_render(slug)

    def create(self, spec: SiteSpec) -> None:
        if (self.root / spec.id).exists():
            raise SiteError("site already exists")
        self._commit(spec.id, spec, 1, 0)

    def add_version(self, slug: str, spec: SiteSpec) -> None:
        meta = self.meta(slug)
        self._commit(slug, spec, meta["head"] + 1, meta["max"])

    def move(self, slug: str, step: int) -> str | None:
        """Undo (-1) or redo (+1). Returns a no-op message at the ends, else None."""
        meta = self.meta(slug)
        head = meta["head"] + step
        if head < 1:
            return "Nothing to undo."
        if head > meta["max"]:
            return "Nothing to redo."
        self._set_head(slug, head, meta["max"])
        return None

    def asset_uri(self, slug: str, name: str) -> str | None:
        try:
            path = self.dir(slug) / "site" / name
            return data_uri(path.name, path.read_bytes())
        except (OSError, SiteError):
            return None

    def html(self, slug: str, *, select: bool = False) -> str:
        return render(
            self.load(slug), load_image=lambda n: self.asset_uri(slug, n), select=select
        )

    def write_render(self, slug: str) -> None:
        _write(self.dir(slug) / "site" / "index.html", self.html(slug))

    def copy_image(self, slug: str, source: str, allowed_roots: list[Path]) -> str:
        """Copy a user image into the site folder (never hot-linked); returns assets/<name>."""
        src = check_image_path(source, allowed_roots)
        name = re.sub(r"[^A-Za-z0-9._-]", "_", src.name)[-80:]
        dest = self.dir(slug) / "site" / "assets" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return f"assets/{name}"


def check_image_path(source: str, allowed_roots: list[Path]) -> Path:
    """File-scope rule for set_image: inside an allowed root, no secrets, an image, small.

    ponytail: local rule set; swap for phoenix.security.trust scope when it lands.
    """
    if not isinstance(source, str) or not source.strip() or "\x00" in source:
        raise SiteError("image path is not valid")
    try:
        path = Path(source).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise SiteError("image file not found") from None
    rel = None
    for root in allowed_roots:
        try:
            rel = path.relative_to(root.resolve())
            break
        except ValueError:
            continue
    if rel is None:
        raise SiteError("image is outside the allowed folders")
    parts = [p.lower() for p in rel.parts]
    name = path.name.lower()
    if (
        any(p in DENIED_PARTS for p in parts)
        or name.startswith(".env")
        or name.endswith((".pem", ".key"))
    ):
        raise SiteError("image is in a protected location")
    if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
        raise SiteError("image must be a png, jpg, gif or webp file")
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise SiteError("image is larger than 5 MB")
    return path
