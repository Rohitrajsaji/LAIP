"""Build a deterministic public source candidate using the reviewed packaging inventory.

Original development files are never modified. Output is confined to
.local/release-candidate/<content-digest>/ and excludes private corpus evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import zipfile
from pathlib import Path

ROOT_FILES = {
    "README.md",
    "Makefile",
    "compose.yaml",
    "compose.test.yaml",
    ".gitignore",
    ".dockerignore",
}
TREES = ("backend/src", "backend/tests", "web/src", "web/tests", "scripts", ".github", "docs")
CONFIGS = {
    "backend/README.md",
    "backend/Dockerfile",
    "backend/.dockerignore",
    "backend/pyproject.toml",
    "backend/uv.lock",
    "web/Dockerfile",
    "web/.dockerignore",
    "web/package.json",
    "web/package-lock.json",
    "web/tsconfig.json",
    "web/next-env.d.ts",
    "web/next.config.ts",
    "web/postcss.config.mjs",
}
IGNORED_PARTS = {
    ".local",
    ".git",
    "node_modules",
    ".next",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
}
EXTRA_EXCLUDED = {"docs/packaging/inventory.json", "docs/packaging/inventory.md"}
RETAIN_HELPERS = {"scripts/test_banking.py", "backend/src/laip/banking_corpus.py"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def regular(path: Path) -> None:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"Not a regular file: {path.name}")


def safe_components(root: Path, path: Path) -> None:
    current = root
    for component in path.relative_to(root).parts:
        current /= component
        if current.is_symlink():
            raise ValueError("Symlink in source/output path")


def selection(root: Path, excluded: set[str]) -> list[Path]:
    selected: set[Path] = set()
    for name in ROOT_FILES | CONFIGS:
        path = root / name
        safe_components(root, path)
        regular(path)
        selected.add(path)
    for tree in TREES:
        base = root / tree
        safe_components(root, base)
        for directory, dirs, files in os.walk(base, followlinks=False):
            for item in dirs + files:
                if (Path(directory) / item).is_symlink():
                    raise ValueError(f"Symlink in allowlisted tree: {tree}")
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_PARTS)
            for name in sorted(files):
                path = Path(directory) / name
                rel = path.relative_to(root).as_posix()
                if (
                    rel in excluded
                    or name == ".DS_Store"
                    or name == ".coverage"
                    or name.endswith((".pyc", ".tsbuildinfo"))
                ):
                    continue
                regular(path)
                if name.startswith(".env"):
                    raise ValueError("Environment file in allowlisted tree")
                selected.add(path)
    return sorted(selected)


def project_markdown(root: Path, path: Path, raw: bytes, excluded: set[str]) -> bytes:
    text = raw.decode("utf-8")
    summary = root / "docs/private-validation.md"

    def replace_link(match: re.Match[str]) -> str:
        target = match.group(2).strip("<>")
        if re.match(r"^[a-zA-Z][\w+.-]*:", target):
            return match.group(0)
        base, _, fragment = target.partition("#")
        resolved = (path.parent / base).resolve() if base else path
        if resolved.is_relative_to(root):
            rel = resolved.relative_to(root).as_posix()
            if rel in excluded or rel.startswith("docs/validation/banking/"):
                resolved, fragment = summary, ""
            elif rel.startswith("media/demo/"):
                resolved, fragment = root / "docs/demo.md", ""
            projected = os.path.relpath(resolved, path.parent)
            return f"{match.group(1)}({projected}" + (f"#{fragment}" if fragment else "") + ")"
        return match.group(0)

    text = re.sub(r"(!?\[[^\]\n]*\])\((<[^>]+>|[^\s)]+)\)", replace_link, text)
    text = re.sub(r'/Users/[^/\s`"<>]+', "/path/to/user", text)
    return text.encode("utf-8")


def build(root: Path) -> Path:
    root = root.resolve()
    inventory_path = root / "docs/packaging/policy.json"
    safe_components(root, inventory_path)
    regular(inventory_path)
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    private = set(inventory["exclude_private_subset"])
    excluded = (private - RETAIN_HELPERS) | EXTRA_EXCLUDED
    files = selection(root, excluded)
    payload: dict[str, bytes] = {}
    entries = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        projected = project_markdown(root, path, raw, excluded) if path.suffix == ".md" else raw
        payload[rel] = projected
        entries.append(
            {
                "path": rel,
                "bytes": len(projected),
                "sha256": digest(projected),
                "original_sha256": digest(raw),
                "transformed": projected != raw,
            }
        )
    if "docs/private-validation.md" not in payload:
        raise ValueError("Public private-validation summary is required")
    manifest = {
        "schema_version": "1.0.0",
        "format": "LAIP public source candidate",
        "policy_sha256": digest(inventory_path.read_bytes()),
        "audit_inventory_sha256": inventory.get("audit_inventory_sha256"),
        "projection": "Markdown local links normalized; "
        "optional media links redirected to demo.md; "
        "withheld links redirected to "
        "private-validation.md; home paths replaced by /path/to/user.",
        "excluded": sorted(excluded),
        "files": entries,
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    identity = digest(manifest_bytes)
    output = root / ".local/release-candidate" / identity
    safe_components(root, output)
    output.mkdir(parents=True, exist_ok=True)
    for name in ("laip-source.zip", "manifest.json", "SHA256SUMS"):
        safe_components(root, output / name)
    archive = output / "laip-source.zip"
    if archive.exists():
        regular(archive)
        if not (output / "manifest.json").exists():
            raise ValueError("Incomplete existing release candidate")
        if (output / "manifest.json").read_bytes() != manifest_bytes:
            raise ValueError("Existing candidate manifest differs")
        with zipfile.ZipFile(archive) as existing:
            if existing.read("RELEASE-MANIFEST.json") != manifest_bytes:
                raise ValueError("Existing archive manifest differs")
            if set(existing.namelist()) != set(payload) | {"RELEASE-MANIFEST.json"}:
                raise ValueError("Existing archive members differ")
            for name, data in payload.items():
                if existing.read(name) != data:
                    raise ValueError("Existing archive content differs")
        return archive
    safe_components(root, output / "manifest.json")
    with archive.open("xb") as stream, zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in sorted({**payload, "RELEASE-MANIFEST.json": manifest_bytes}.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            bundle.writestr(info, data)
    (output / "manifest.json").write_bytes(manifest_bytes)
    (output / "SHA256SUMS").write_text(f"{digest(archive.read_bytes())}  laip-source.zip\n")
    return archive


if __name__ == "__main__":
    print(build(Path(__file__).resolve().parents[1]))
