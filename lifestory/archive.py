"""Archive persistence (spec v2 §11 "Data pattern", §12.1).

Local-first by design. A case is a directory of plain files on the operator's
machine; nothing is uploaded anywhere except the specific text sent to the
model during extraction and drafting, which is gated on consent (§12.1).

Every write bumps a revision and keeps the previous version, so generated
content can reference an immutable archive version (§11).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .models import Archive, CaseMeta

CASES_DIRNAME = "cases"
ARCHIVE_FILENAME = "archive.json"
VERSIONS_DIRNAME = "versions"

# How many prior revisions to keep on disk. Enough to undo a bad session's
# work; not so many that a case quietly accumulates hundreds of megabytes.
KEEP_VERSIONS = 30


class CasePaths:
    """Layout of one case on disk."""

    def __init__(self, root: Path, case_id: str) -> None:
        self.root = root
        self.case_id = case_id
        self.dir = root / CASES_DIRNAME / case_id

    @property
    def archive(self) -> Path:
        return self.dir / ARCHIVE_FILENAME

    @property
    def versions(self) -> Path:
        return self.dir / VERSIONS_DIRNAME

    @property
    def transcripts(self) -> Path:
        return self.dir / "transcripts"

    @property
    def photos(self) -> Path:
        """Originals. Never written to -- derivatives go in `derived` (§12.1)."""
        return self.dir / "photos" / "originals"

    @property
    def derived(self) -> Path:
        return self.dir / "photos" / "derived"

    @property
    def drafts(self) -> Path:
        return self.dir / "drafts"

    @property
    def exports(self) -> Path:
        return self.dir / "exports"

    def ensure(self) -> None:
        for path in (
            self.dir,
            self.versions,
            self.transcripts,
            self.photos,
            self.derived,
            self.drafts,
            self.exports,
        ):
            path.mkdir(parents=True, exist_ok=True)


def project_root(start: Path | None = None) -> Path:
    """Find the project root by walking up for a `cases/` directory."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / CASES_DIRNAME).is_dir():
            return candidate
    return here


def list_cases(root: Path | None = None) -> list[str]:
    root = root or project_root()
    cases_dir = root / CASES_DIRNAME
    if not cases_dir.is_dir():
        return []
    return sorted(
        p.name for p in cases_dir.iterdir() if p.is_dir() and (p / ARCHIVE_FILENAME).exists()
    )


def create_case(meta: CaseMeta, root: Path | None = None) -> tuple[Archive, CasePaths]:
    root = root or project_root()
    paths = CasePaths(root, meta.id)
    if paths.archive.exists():
        raise FileExistsError(f"case '{meta.id}' already exists at {paths.dir}")
    paths.ensure()
    archive = Archive(meta=meta)
    save(archive, paths)
    return archive, paths


def load(case_id: str, root: Path | None = None) -> tuple[Archive, CasePaths]:
    root = root or project_root()
    paths = CasePaths(root, case_id)
    if not paths.archive.exists():
        known = ", ".join(list_cases(root)) or "none"
        raise FileNotFoundError(f"no case '{case_id}' (known cases: {known})")
    data = json.loads(paths.archive.read_text(encoding="utf-8"))
    return Archive.model_validate(data), paths


def _payload_without_volatile(archive: Archive) -> str:
    """Serialised archive with the fields that change on every write removed.

    Used to tell a real edit from a no-op save, so that running a read-only
    command does not manufacture a new revision.
    """
    payload = archive.model_dump(mode="json", exclude_none=False)
    payload.pop("revision", None)
    payload.pop("updated_at", None)
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)


def _prune_versions(paths: CasePaths) -> None:
    """Keep the most recent versions and discard the rest.

    A full archive copy per revision is ~800KB on a normal Package B case;
    keeping every one of them cost tens of megabytes within a single case.
    """
    versions = sorted(paths.versions.glob("r*.json"))
    for stale in versions[:-KEEP_VERSIONS]:
        stale.unlink(missing_ok=True)


def save(archive: Archive, paths: CasePaths, *, keep_version: bool = True) -> Path:
    """Write the archive, preserving the previous revision.

    Versions are kept so a delivered book can name the exact archive revision
    it was generated from (§11). A save that would change nothing is skipped
    entirely, revision and all.
    """
    paths.ensure()

    content = _payload_without_volatile(archive)

    if paths.archive.exists():
        try:
            current = json.loads(paths.archive.read_text(encoding="utf-8"))
            current.pop("revision", None)
            current.pop("updated_at", None)
            if json.dumps(current, indent=2, ensure_ascii=False, sort_keys=True) == content:
                return paths.archive
        except (json.JSONDecodeError, OSError):
            pass  # unreadable current file: fall through and overwrite it

        if keep_version:
            prior = paths.versions / f"r{archive.revision:04d}.json"
            if not prior.exists():
                shutil.copy2(paths.archive, prior)
            _prune_versions(paths)

    archive.revision += 1
    archive.updated_at = datetime.now(timezone.utc)

    payload = archive.model_dump(mode="json", exclude_none=False)
    tmp = paths.archive.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(paths.archive)
    return paths.archive


def export_portable(archive: Archive, paths: CasePaths) -> Path:
    """Full archive export in a durable, non-proprietary format (§12.1).

    This is what the family receives if they ever ask for everything, and what
    transfers to the named inheritor under §12.4.
    """
    paths.ensure()
    out = paths.exports / f"{archive.meta.id}-archive-r{archive.revision}.json"
    payload = archive.model_dump(mode="json", exclude_none=False)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out
