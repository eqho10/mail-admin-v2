"""Mailbox list_all aggregate from HestiaCP CLI (mocked)."""


def test_list_all_aggregates_domains(monkeypatch):
    fake_domains_json = '{"bilgeworld.com":{},"radagida.com":{}}'
    fake_accounts_bilge = '{"info":{"DISK_USED":"5"},"noreply":{"DISK_USED":"2"}}'
    fake_accounts_rada = '{"orders":{"DISK_USED":"10"}}'

    def fake_sh(cmd, timeout=10):
        if 'v-list-mail-domains' in cmd[0]:
            return fake_domains_json
        if 'v-list-mail-accounts' in cmd[0] and 'bilgeworld.com' in cmd:
            return fake_accounts_bilge
        if 'v-list-mail-accounts' in cmd[0] and 'radagida.com' in cmd:
            return fake_accounts_rada
        return ''

    import services.mailboxes
    monkeypatch.setattr(services.mailboxes, '_sh', fake_sh)
    result = services.mailboxes.list_all()
    assert sorted(result) == sorted([
        'info@bilgeworld.com',
        'noreply@bilgeworld.com',
        'orders@radagida.com',
    ])


def test_list_all_handles_empty(monkeypatch):
    import services.mailboxes
    monkeypatch.setattr(services.mailboxes, '_sh', lambda cmd, timeout=10: '')
    result = services.mailboxes.list_all()
    assert result == []
