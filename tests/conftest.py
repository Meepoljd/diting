import shutil
import textwrap
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "shop"


@pytest.fixture
def shop(tmp_path: Path) -> Path:
    """A copy of the example repo where the same rounding rule lives in three places."""
    dst = tmp_path / "shop"
    shutil.copytree(EXAMPLE, dst, ignore=shutil.ignore_patterns(".diting", "__pycache__"))
    return dst


def write(path: Path, src: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(src).lstrip(), encoding="utf-8")
