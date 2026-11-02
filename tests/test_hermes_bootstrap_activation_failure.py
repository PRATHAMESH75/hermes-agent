"""The line a failed launch prints names the failing step and call, and a remedy that fits (#147781).

Real: ``hermes_bootstrap``'s import-time activation block, in a child interpreter. Seam: the
activation steps it calls, which raise what a real failure raises.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _launch(tmp_path, failing_step: str, raised: str) -> subprocess.CompletedProcess:
    program = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(ROOT)!r})
        sys.argv = ['hermes', 'status']
        import hermes_cli._early_recovery as recovery, hermes_cli.venv_sync as venv_sync, pm.environments as envs
        def fail(*_args):
            raise {raised}
        venv_sync.prepare_launch = lambda *_args: None
        recovery.recover_if_needed = lambda *_args: None
        envs.activate_dependencies = lambda *_args: None
        {failing_step} = fail
        import hermes_bootstrap
    """)
    return subprocess.run([sys.executable, "-c", program], capture_output=True, encoding="utf-8", timeout=120,
                          check=False, cwd=tmp_path, env={**os.environ, "HERMES_HOME": str(tmp_path / "home")})


@pytest.mark.parametrize(("failing_step", "named"), [
    ("recovery.recover_if_needed", "startup recovery failed"),
    ("envs.activate_dependencies", "dependency activation failed"),
])
def test_failure_names_the_step_and_keeps_the_repair_remedy(tmp_path, failing_step, named):
    result = _launch(tmp_path, failing_step, "RuntimeError('no site-packages')")
    assert result.returncode == 1
    assert named in result.stderr
    assert "no site-packages" in result.stderr
    assert "(at " in result.stderr and "hermes_bootstrap.py:" in result.stderr
    assert "run `hermes pm repair`" in result.stderr


def test_fileless_enoent_points_at_the_cwd_instead_of_a_dependency_rebuild(tmp_path):
    result = _launch(tmp_path, "envs.activate_dependencies",
                     "FileNotFoundError(2, 'No such file or directory')")
    assert result.returncode == 1
    assert "working directory" in result.stderr
    assert "pm repair" not in result.stderr


def test_enoent_naming_a_path_keeps_the_repair_remedy(tmp_path):
    result = _launch(tmp_path, "envs.activate_dependencies",
                     "FileNotFoundError(2, 'No such file or directory', '/gone/site-packages')")
    assert result.returncode == 1
    assert "/gone/site-packages" in result.stderr
    assert "run `hermes pm repair`" in result.stderr
