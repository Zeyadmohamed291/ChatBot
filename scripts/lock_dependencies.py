"""Record tested installed versions, including transitive dependencies, without local paths."""

import importlib.metadata as metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def collect(names):
    versions = {}
    pending = list(names)
    while pending:
        name = canonicalize_name(pending.pop())
        if name in versions:
            continue
        dist = metadata.distribution(name)
        versions[name] = dist.version
        for raw in dist.requires or []:
            req = Requirement(raw)
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                pending.append(req.name)
    return versions


if __name__ == "__main__":
    runtime = ["fastapi", "uvicorn", "httpx", "openai", "pydantic-settings", "redis"]
    development = runtime + ["pytest", "pytest-asyncio", "ruff", "fakeredis", "lupa", "packaging"]
    for filename, roots in [("requirements.lock", runtime), ("requirements-dev.lock", development)]:
        versions = collect(roots)
        content = "# Tested Python 3.12 versions. Regenerate after deliberate upgrades.\n"
        content += (
            "\n".join(f"{name}=={version}" for name, version in sorted(versions.items())) + "\n"
        )
        Path(filename).write_text(content, encoding="utf-8")
        print(f"{filename}: {len(versions)} pinned distributions")
