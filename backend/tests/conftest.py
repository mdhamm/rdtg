from pathlib import Path

import pytest

from rdtg.project import open_project

DEMO = Path(__file__).resolve().parents[2] / "examples" / "engine_controller" / "workspace"


@pytest.fixture
def demo():
    return open_project(DEMO)
