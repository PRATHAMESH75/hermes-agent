"""Which install-state files make a channel record "claimed" (#147758).

Doctor tells the user a stale record is safe to remove, and channel resolution
reads that record, so a false "unclaimed" costs a healthy install its channel.
"""

import pytest

from hermes_cli.update_channel import install_id, stale_channel_records
from pm.environments import install_state_dir


@pytest.mark.parametrize('marker, expected', [
    ('facts.json', []),  # what PM provisioning writes
    ('install.json', []),  # what older installs still hold
    (None, ['unclaimed']),
])
def test_channel_record_is_claimed_by_install_state(tmp_path, monkeypatch, marker, expected):
    root = tmp_path / 'install'
    root.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / '.hermes'))
    if marker:
        state = install_state_dir(root)
        state.mkdir(parents=True)
        (state / marker).write_text('{}', encoding='utf-8')
    config = {'update': {'installs': {install_id(root): {'path': str(root), 'channel': 'main'}}}}
    assert [reason for _, _, reason in stale_channel_records(config)] == expected
