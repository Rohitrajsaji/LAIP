"""Public-source packaging boundaries and deterministic archive regression checks."""

from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


package = load_script("package_release")
checker = load_script("check_docs")


def source_root(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    for rel in package.ROOT_FILES | package.CONFIGS:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    for tree in package.TREES:
        (root / tree).mkdir(parents=True, exist_ok=True)
    (root / "docs/packaging").mkdir()
    (root / "docs/packaging/policy.json").write_text(
        json.dumps(
            {
                "exclude_private_subset": ["docs/private-report.md"],
            }
        )
    )
    (root / "docs/private-validation.md").write_text("# Private validation\n")
    (root / "docs/private-report.md").write_text("PRIVATE-CONTENT")
    (root / "README.md").write_text("[Validation](docs/private-report.md)\n")
    (root / "backend/src/source.py").write_text('print("synthetic")\n')
    (root / ".local").mkdir()
    (root / ".local/secret").write_text("NEVER-PUBLISH")
    (root / "web/node_modules").mkdir()
    (root / "web/node_modules/installed.js").write_text("NEVER-PUBLISH")
    return root


def test_candidate_excludes_private_and_projects_with_hashes(tmp_path: Path) -> None:
    root = source_root(tmp_path)
    archive = package.build(root)
    first = archive.read_bytes()
    assert package.build(root).read_bytes() == first
    with zipfile.ZipFile(archive) as bundle:
        assert "docs/private-report.md" not in bundle.namelist()
        assert not any(".local/" in name or "node_modules/" in name for name in bundle.namelist())
        assert b"PRIVATE-CONTENT" not in b"".join(bundle.read(n) for n in bundle.namelist())
        assert bundle.read("README.md") == b"[Validation](docs/private-validation.md)\n"
        manifest = json.loads(bundle.read("RELEASE-MANIFEST.json"))
        entry = next(row for row in manifest["files"] if row["path"] == "README.md")
        assert entry["transformed"] and entry["sha256"] != entry["original_sha256"]
    assert (root / "README.md").read_text() == "[Validation](docs/private-report.md)\n"


def test_candidate_rejects_source_and_output_symlinks(tmp_path: Path) -> None:
    root = source_root(tmp_path)
    (root / "backend/src/link").symlink_to(root / ".local/secret")
    with pytest.raises(ValueError, match="Symlink"):
        package.build(root)
    (root / "backend/src/link").unlink()
    (root / ".local/release-candidate").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="Symlink"):
        package.build(root)


def test_candidate_rejects_environment_and_special_files(tmp_path: Path) -> None:
    root = source_root(tmp_path)
    (root / "backend/src/.env").write_text("NEVER-READ")
    with pytest.raises(ValueError, match="Environment"):
        package.build(root)


def test_link_checker_checks_missing_files_anchors_and_ignores_code(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "# Intro\n[Good](guide.md#details)\n[Bad](missing.md)\n"
        "[Remote](https://example.com)\n```md\n[Example](fiction.md)\n```\n"
    )
    (tmp_path / "guide.md").write_text("# Details\n")
    errors = checker.check(tmp_path)
    assert len(errors) == 1 and "missing.md" in errors[0]


def test_clean_candidate_rebuild_uses_public_policy_only(tmp_path: Path) -> None:
    root = source_root(tmp_path)
    archive = package.build(root)
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(extracted)
    assert not (extracted / "docs/packaging/inventory.json").exists()
    assert (extracted / "docs/packaging/policy.json").is_file()
    rebuilt = package.build(extracted)
    with zipfile.ZipFile(rebuilt) as bundle:
        assert bundle.read("README.md") == b"[Validation](docs/private-validation.md)\n"
        assert "docs/private-report.md" not in bundle.namelist()
    assert checker.check(extracted) == []
