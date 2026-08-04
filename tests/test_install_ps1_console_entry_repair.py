"""Windows installer regression coverage for missing console entry points."""

from pathlib import Path

import pytest

_INSTALL_PS1 = Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"


@pytest.fixture(scope="module")
def install_dependencies() -> str:
    source = _INSTALL_PS1.read_text(encoding="utf-8")
    start = source.index("function Install-Dependencies")
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace : index + 1]
    raise AssertionError("unterminated Install-Dependencies function")


def test_entry_point_repair_falls_back_to_venv_pip_without_dependencies(
    install_dependencies: str,
):
    uv_repair = "& $UvCmd pip install --reinstall -e ."
    pip_fallback = (
        "& $pythonExe -m pip install --force-reinstall --no-deps -e ."
    )

    assert uv_repair in install_dependencies
    assert pip_fallback in install_dependencies
    assert install_dependencies.index(uv_repair) < install_dependencies.index(pip_fallback)


def test_entry_point_repair_fails_install_if_launchers_remain_missing(
    install_dependencies: str,
):
    pip_fallback = (
        "& $pythonExe -m pip install --force-reinstall --no-deps -e ."
    )
    fatal_postcondition = "throw \"Console entry points remain missing"

    assert fatal_postcondition in install_dependencies
    assert install_dependencies.index(pip_fallback) < install_dependencies.index(
        fatal_postcondition
    )


def test_entry_point_repair_fails_if_pip_fallback_exits_nonzero(
    install_dependencies: str,
):
    pip_fallback = (
        "& $pythonExe -m pip install --force-reinstall --no-deps -e ."
    )
    capture_exit = "$pipFallbackExit = $LASTEXITCODE"
    reject_failure = "if ($pipFallbackExit -ne 0)"

    fallback_index = install_dependencies.index(pip_fallback)
    capture_index = install_dependencies.index(capture_exit)
    reject_index = install_dependencies.index(reject_failure)

    assert fallback_index < capture_index < reject_index


def test_entry_point_repair_bootstraps_pip_before_fallback(
    install_dependencies: str,
):
    pip_probe = "& $pythonExe -m pip --version"
    ensurepip = "& $pythonExe -m ensurepip --upgrade"
    ensurepip_exit = "$ensurePipExit = $LASTEXITCODE"
    reject_ensurepip_failure = "if ($ensurePipExit -ne 0)"
    pip_fallback = (
        "& $pythonExe -m pip install --force-reinstall --no-deps -e ."
    )

    probe_index = install_dependencies.index(pip_probe)
    ensurepip_index = install_dependencies.index(ensurepip)
    exit_index = install_dependencies.index(ensurepip_exit)
    reject_index = install_dependencies.index(reject_ensurepip_failure)
    fallback_index = install_dependencies.index(pip_fallback)

    assert probe_index < ensurepip_index < exit_index < reject_index < fallback_index
