import sys
import os

# Ensure src and application root are always importable
repo_dir = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(repo_dir, "src"))
sys.path.insert(0, repo_dir)

from loki.cli import app

if __name__ == "__main__":
    app()
