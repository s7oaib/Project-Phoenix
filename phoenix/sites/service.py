"""Async build/edit jobs over SiteStore: one job at a time, phases planning ->
spec -> render -> ready. Clients poll ``get``; a failed or rejected job leaves
the stored site untouched. Job state is in memory only.
"""

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from phoenix.sites.generator import ModelFn, build_spec, edit_spec
from phoenix.sites.spec import SiteError
from phoenix.sites.store import SiteStore

STOP = {
    "a",
    "an",
    "me",
    "my",
    "the",
    "new",
    "design",
    "build",
    "make",
    "create",
    "generate",
    "website",
    "web",
    "site",
    "landing",
    "page",
    "for",
    "please",
    "can",
    "you",
}


class SiteNotFound(SiteError):
    pass


class SiteBusy(SiteError):
    pass


@dataclass
class Job:
    status: str = "building"  # building | ready | failed
    phase: str = "planning"  # planning | spec | render | ready
    error: str | None = None
    message: str = ""


class SitesService:
    def __init__(
        self,
        root_fn: Callable[[], Path],
        model_fn: Callable[[], ModelFn | None],
        image_roots_fn: Callable[[], list[Path]] | None = None,
    ) -> None:
        self._root_fn = root_fn
        self._model_fn = model_fn
        self._roots_fn = image_roots_fn or (lambda: [root_fn()])
        self._jobs: dict[str, Job] = {}
        self._active: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def store(self) -> SiteStore:
        return SiteStore(self._root_fn() / "outputs" / "sites")

    @property
    def busy(self) -> bool:
        return self._active is not None and not self._active.done()

    def _start(self, slug: str, work: Callable[[Job], Any]) -> dict[str, Any]:
        if self.busy:
            raise SiteBusy("Still working on the last request.")
        job = self._jobs[slug] = Job()

        async def run() -> None:
            try:
                await work(job)
            except Exception:
                job.status, job.error = (
                    "failed",
                    "Something went wrong while building the site.",
                )

        self._active = task = asyncio.get_running_loop().create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return self.get(slug)

    def start_build(self, prompt: str) -> dict[str, Any]:
        if not prompt.strip():
            raise SiteError("describe the site you want")
        if self.busy:
            raise SiteBusy("Still working on the last request.")
        words = [w for w in re.findall(r"[a-z0-9]+", prompt.lower()) if w not in STOP][
            :4
        ]
        slug = self.store.unique_slug("-".join(words) or "site")

        async def work(job: Job) -> None:
            await asyncio.sleep(0)
            job.phase = "spec"
            spec = await build_spec(prompt, slug, self._model_fn())
            job.phase = "render"
            self.store.create(spec)
            job.status, job.phase = "ready", "ready"
            job.message = f"Site ready. {len(spec.sections)} sections."

        return self._start(slug, work)

    def start_edit(
        self, slug: str, instruction: str, section_id: str | None = None
    ) -> dict[str, Any]:
        store = self.store
        if not store.exists(slug):
            raise SiteNotFound("site not found")
        if not instruction.strip():
            raise SiteError("say what to change")
        if section_id is not None and section_id not in {
            s.id for s in store.load(slug).sections
        }:
            raise SiteError(f"there is no section {section_id}")

        async def work(job: Job) -> None:
            await asyncio.sleep(0)
            job.phase = "spec"
            spec = store.load(slug)
            new, message = await edit_spec(
                spec,
                instruction,
                section_id,
                self._model_fn(),
                lambda path: store.copy_image(slug, path, self._roots_fn()),
            )
            if new is None:
                job.status, job.phase, job.error = "failed", "ready", message
                return
            job.phase = "render"
            store.add_version(slug, new)
            job.status, job.phase, job.message = "ready", "ready", message

        return self._start(slug, work)

    def get(self, slug: str) -> dict[str, Any]:
        store, job = self.store, self._jobs.get(slug)
        if store.exists(slug):
            view = store.summary(slug)
            view["spec"] = store.load(slug).model_dump(mode="json")
        elif job is not None:
            view = {
                "id": slug,
                "name": None,
                "type": None,
                "head": 0,
                "max": 0,
                "spec": None,
            }
        else:
            raise SiteNotFound("site not found")
        view.pop("updated_at", None)
        job = job or Job("ready", "ready")
        return {
            **view,
            "status": job.status,
            "phase": job.phase,
            "error": job.error,
            "message": job.message or None,
        }

    def list(self) -> list[dict[str, Any]]:
        return self.store.list_sites()

    def move(self, slug: str, step: int) -> dict[str, Any]:
        if self.busy:
            raise SiteBusy("Still working on the last request.")
        if not self.store.exists(slug):
            raise SiteNotFound("site not found")
        message = self.store.move(slug, step)
        self._jobs.pop(slug, None)
        view = self.get(slug)
        view["message"] = message or ("Undone." if step < 0 else "Redone.")
        return view

    def preview(self, slug: str) -> str:
        if not self.store.exists(slug):
            raise SiteNotFound("site not found")
        return self.store.html(slug, select=True)

    def find(self, text: str) -> str | None:
        """Slug of the site whose name or id best matches the words in text."""
        words = {
            w
            for w in re.findall(r"[a-z0-9]+", text.lower())
            if w not in STOP | {"open", "show", "my"}
        }
        best, score = None, 0
        for site in self.list():
            have = set(re.findall(r"[a-z0-9]+", f"{site['name']} {site['id']}".lower()))
            if len(words & have) > score:
                best, score = site["id"], len(words & have)
        return best


_default: SitesService | None = None


def set_default(service: SitesService | None) -> None:
    global _default
    _default = service


def get_default() -> SitesService | None:
    return _default


__all__ = ["SiteBusy", "SiteNotFound", "SitesService", "get_default", "set_default"]
