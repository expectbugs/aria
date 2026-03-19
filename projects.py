"""Project status briefs — reads structured markdown files from data/projects/."""

from pathlib import Path
from typing import Optional

from config import DATA_DIR

PROJECTS_DIR = DATA_DIR / "projects"


def list_projects() -> list[str]:
    """Return names of available project briefs (filename stems)."""
    if not PROJECTS_DIR.exists():
        return []
    return sorted(p.stem for p in PROJECTS_DIR.glob("*.md"))


def get_project(name: str) -> Optional[str]:
    """Read a project brief by name. Returns file contents or None."""
    path = PROJECTS_DIR / f"{name}.md"
    if not path.exists():
        # Try case-insensitive match
        for p in PROJECTS_DIR.glob("*.md"):
            if p.stem.lower() == name.lower():
                return p.read_text()
        return None
    return path.read_text()


def find_project(query: str) -> Optional[tuple[str, str]]:
    """Find a project matching a query string. Returns (name, contents) or None."""
    query_lower = query.lower()
    for p in PROJECTS_DIR.glob("*.md"):
        if p.stem.lower() in query_lower or query_lower in p.stem.lower():
            return (p.stem, p.read_text())
    return None
