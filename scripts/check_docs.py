"""Check local Markdown links without fetching remote resources."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

LINK = re.compile(r'!?\[[^\]\n]*\]\((<[^>]+>|[^\s)]+)(?:\s+"[^"]*")?\)')


def prose(text: str) -> str:
    return re.sub(r"(?ms)^\s*(```|~~~).*?^\s*\1[^\n]*$", "", text)


def anchors(text: str) -> set[str]:
    found: set[str] = set()
    counts: dict[str, int] = {}
    for heading in re.findall(r"(?m)^#{1,6}\s+(.+?)\s*#*$", prose(text)):
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        found.add(slug + (f"-{count}" if count else ""))
    found.update(re.findall(r'<a\s+(?:id|name)=["\']([^"\']+)', text))
    return found


def check(root: Path) -> list[str]:
    root = root.resolve()
    errors: list[str] = []
    policy_path = root / "docs/packaging/policy.json"
    excluded: set[str] = set()
    if policy_path.is_file() and not policy_path.is_symlink():
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        excluded = set(policy["exclude_private_subset"])
    for document in sorted(root.rglob("*.md")):
        if document.relative_to(root).as_posix() in excluded or document.relative_to(
            root
        ).as_posix().startswith("docs/licenses/runtime/"):
            continue
        if any(
            part in {".local", "node_modules", ".venv", ".next", "__pycache__"}
            for part in document.relative_to(root).parts
        ):
            continue
        if document.is_symlink():
            errors.append(f"{document.relative_to(root)}: symlink document")
            continue
        for match in LINK.finditer(prose(document.read_text(encoding="utf-8"))):
            destination = unquote(match.group(1).strip("<>"))
            parsed = urlsplit(destination)
            if parsed.scheme or parsed.netloc:
                continue
            path = (document.parent / parsed.path).resolve() if parsed.path else document
            if not path.is_relative_to(root):
                errors.append(f"{document.relative_to(root)}: external local link {destination}")
            elif not path.exists():
                errors.append(f"{document.relative_to(root)}: missing {destination}")
            elif (
                parsed.fragment
                and path.suffix == ".md"
                and parsed.fragment not in anchors(path.read_text(encoding="utf-8"))
            ):
                errors.append(f"{document.relative_to(root)}: missing anchor {destination}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path("."))
    args = parser.parse_args()
    errors = check(args.root)
    for error in errors:
        print(error)
    print(
        f"Public authored Markdown link check (policy exclusions/vendor notices skipped): "
        f"{len(errors)} errors"
    )
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
