import run
from cnkibug.version import APP_VERSION


def test_self_check_reports_app_version(capsys):
    assert run._run_self_check() == 0
    assert capsys.readouterr().out.strip() == f"CNKIBug self-check OK: {APP_VERSION}"
