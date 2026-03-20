"""Tests for projects.py — project status briefs."""

from unittest.mock import patch, MagicMock
from pathlib import Path

import projects


class TestListProjects:
    def test_returns_project_names(self, tmp_path):
        proj_dir = tmp_path / "projects"
        proj_dir.mkdir()
        (proj_dir / "aria.md").write_text("# ARIA")
        (proj_dir / "website.md").write_text("# Website")
        (proj_dir / "not_md.txt").write_text("skip")

        with patch.object(projects, "PROJECTS_DIR", proj_dir):
            names = projects.list_projects()
        assert "aria" in names
        assert "website" in names
        assert "not_md" not in names

    def test_no_directory(self, tmp_path):
        with patch.object(projects, "PROJECTS_DIR", tmp_path / "missing"):
            assert projects.list_projects() == []


class TestGetProject:
    def test_exact_match(self, tmp_path):
        proj_dir = tmp_path / "projects"
        proj_dir.mkdir()
        (proj_dir / "aria.md").write_text("# ARIA Project")

        with patch.object(projects, "PROJECTS_DIR", proj_dir):
            content = projects.get_project("aria")
        assert content == "# ARIA Project"

    def test_case_insensitive(self, tmp_path):
        proj_dir = tmp_path / "projects"
        proj_dir.mkdir()
        (proj_dir / "ARIA.md").write_text("# ARIA")

        with patch.object(projects, "PROJECTS_DIR", proj_dir):
            content = projects.get_project("aria")
        assert content == "# ARIA"

    def test_not_found(self, tmp_path):
        proj_dir = tmp_path / "projects"
        proj_dir.mkdir()

        with patch.object(projects, "PROJECTS_DIR", proj_dir):
            assert projects.get_project("nonexistent") is None


class TestFindProject:
    def test_finds_by_name_in_query(self, tmp_path):
        proj_dir = tmp_path / "projects"
        proj_dir.mkdir()
        (proj_dir / "aria.md").write_text("# ARIA Details")

        with patch.object(projects, "PROJECTS_DIR", proj_dir):
            result = projects.find_project("what's the status of aria")
        assert result is not None
        name, contents = result
        assert name == "aria"
        assert "ARIA Details" in contents

    def test_no_match(self, tmp_path):
        proj_dir = tmp_path / "projects"
        proj_dir.mkdir()
        (proj_dir / "other.md").write_text("# Other")

        with patch.object(projects, "PROJECTS_DIR", proj_dir):
            assert projects.find_project("status of nonexistent") is None
