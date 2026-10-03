import json
from pathlib import Path
import pytest

from src.loki.engine.scanner import ProjectScanner


class TestProjectScannerDockerDetection:
    def test_detect_stack_dockerfile(self, tmp_path):
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM python:3.11\n", encoding="utf-8")

        scanner = ProjectScanner(project_root=str(tmp_path))
        stack = scanner.detect_stack()

        assert "Docker / Container" in stack["languages"]
        assert "Dockerfile" in stack["detected_files"]

    @pytest.mark.parametrize(
        "compose_filename",
        [
            "docker-compose.yml",
            "docker-compose.yaml",
            "compose.yml",
            "compose.yaml",
        ],
    )
    def test_detect_stack_compose_variants(self, tmp_path, compose_filename):
        compose_file = tmp_path / compose_filename
        compose_file.write_text("services:\n  web:\n    image: nginx\n", encoding="utf-8")

        scanner = ProjectScanner(project_root=str(tmp_path))
        stack = scanner.detect_stack()

        assert "Docker / Container" in stack["languages"]
        assert compose_filename in stack["detected_files"]

    def test_detect_stack_polyglot_python_and_docker(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("pytest\n", encoding="utf-8")
        (tmp_path / "Dockerfile").write_text("FROM python:3.11\n", encoding="utf-8")
        (tmp_path / "compose.yaml").write_text("services:\n", encoding="utf-8")

        scanner = ProjectScanner(project_root=str(tmp_path))
        stack = scanner.detect_stack()

        assert "Python" in stack["languages"]
        assert "Docker / Container" in stack["languages"]
        assert "requirements.txt" in stack["detected_files"]
        assert "Dockerfile" in stack["detected_files"]
        assert "compose.yaml" in stack["detected_files"]

    def test_initialize_records_docker_in_knowledge_json(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM alpine\n", encoding="utf-8")

        scanner = ProjectScanner(project_root=str(tmp_path))
        paths = scanner.initialize()

        knowledge_file = Path(paths["knowledge"])
        assert knowledge_file.exists()

        data = json.loads(knowledge_file.read_text(encoding="utf-8"))
        assert "Docker / Container" in data["stack"]["languages"]
        assert "Dockerfile" in data["stack"]["detected_files"]
