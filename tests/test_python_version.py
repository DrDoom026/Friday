"""Guard against the local interpreter drifting from the deployed one.

Part 5 shipped a bug that every local test passed over: a method named ``list``
shadowed the builtin inside a class body, breaking the ``-> list[...]``
annotations below it. Python 3.12 evaluates annotations eagerly and raised at
import; 3.14 defers them (PEP 649) and did not. The suite was green on a 3.14
venv while the 3.12 container crash-looped on startup.

Docs alone do not stop that recurring, so this test does. The Dockerfile is the
single source of truth - it is what actually runs in production - and these
assertions fail the moment a developer's interpreter stops matching it.
"""

import re
import sys
from pathlib import Path

import pytest

DOCKERFILE = Path(__file__).resolve().parent.parent / "Dockerfile"
PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def deployed_python_version() -> tuple[int, int]:
    """The (major, minor) the Dockerfile builds on."""
    match = re.search(r"^FROM\s+python:(\d+)\.(\d+)", DOCKERFILE.read_text(), re.MULTILINE)
    assert match, f"no 'FROM python:X.Y' base image found in {DOCKERFILE}"
    return int(match.group(1)), int(match.group(2))


def test_dockerfile_pins_an_explicit_python_minor_version():
    """A floating base image would defeat the point of this guard."""
    major, minor = deployed_python_version()

    assert (major, minor) >= (3, 12)


def test_local_interpreter_matches_the_deployed_one():
    """The interpreter running these tests must be the one that runs in Docker.

    If this fails, the tests are not exercising the code as deployed. Recreate
    the venv on the version the Dockerfile names (see README, 'Run locally') -
    do not just relax this assertion.
    """
    expected = deployed_python_version()
    actual = sys.version_info[:2]

    assert actual == expected, (
        f"local Python is {actual[0]}.{actual[1]} but the Dockerfile deploys on "
        f"{expected[0]}.{expected[1]}. Annotation and stdlib behaviour differ between "
        f"versions, so a green suite here would not mean a working container."
    )


def test_pyproject_requires_the_deployed_version():
    """The declared floor must not drift below what is actually deployed."""
    major, minor = deployed_python_version()
    content = PYPROJECT.read_text()

    match = re.search(r'requires-python\s*=\s*"([^"]+)"', content)
    assert match, "pyproject.toml must declare requires-python"
    assert f"{major}.{minor}" in match.group(1), (
        f"requires-python {match.group(1)!r} does not mention the deployed "
        f"{major}.{minor}"
    )


@pytest.mark.skipif(sys.version_info >= (3, 14), reason="PEP 649 defers annotations")
def test_class_body_annotations_are_evaluated_eagerly():
    """The exact failure mode the version gap hid.

    On the deployed interpreter a name bound in a class body shadows a builtin
    for every annotation after it. This test documents that this is load-bearing
    behaviour, not a curiosity - and it is why the versions must match.
    """
    with pytest.raises(TypeError, match="not subscriptable"):
        exec(
            "class Shadowed:\n"
            "    def list(self) -> list: ...\n"
            "    def describe(self) -> list[dict]: ...\n",
            {},
        )
