from unittest.mock import Mock

import pytest

from cnkibug.gui import updater
from cnkibug.gui.update_dialog import UpdateDialog


@pytest.mark.parametrize("newer,ready,expected_state", [
    (False, True, "normal"),
    (True, True, "normal"),
    (True, False, "disabled"),
])
def test_update_result_buttons_match_release_state(monkeypatch, newer, ready, expected_state):
    monkeypatch.setattr(updater, "can_install_update", lambda: True)
    dialog = UpdateDialog.__new__(UpdateDialog)
    for name in ("_status", "_progress", "_notes", "_notes_view", "_primary", "_secondary"):
        setattr(dialog, name, Mock())
    release = updater.ReleaseInfo(
        version="0.6.0",
        page_url="https://example.test/release",
        published_at="2026-09-14T00:00:00Z",
        notes="Release notes",
        newer=newer,
        download_url="https://example.test/gui.exe" if ready else "",
        size=10,
        sha256="a" * 64,
    )

    dialog._show_release(release)

    assert dialog._primary.configure.call_args.kwargs["state"] == expected_state
