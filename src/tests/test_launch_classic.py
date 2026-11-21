import json
import socket
from unittest.mock import Mock

import pytest

import launch_classic as launcher


def test_port_guard():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        with pytest.raises(RuntimeError, match='already in use'):
            launcher.check_port(sock.getsockname()[1])


def test_readiness_authenticates_expected_instance(monkeypatch):
    server = Mock()
    server.poll.return_value = None
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.side_effect = [json.dumps({'status': 'ok', 'launch_id': 'wrong'}).encode(),
                                 json.dumps({'status': 'ok', 'launch_id': 'correct'}).encode()]
    urlopen = Mock(return_value=response)
    monkeypatch.setattr(launcher.urllib.request, 'urlopen', urlopen)
    monkeypatch.setattr(launcher.time, 'sleep', lambda _: None)
    launcher.wait_ready(server, 'http://127.0.0.1:8000', 'correct')
    assert urlopen.call_count == 2


def test_startup_failed_no_tunnel(monkeypatch):
    proc = Mock()
    proc.poll.return_value = 1
    proc.returncode = 1
    popen = Mock(return_value=proc)
    monkeypatch.setattr(launcher.subprocess, 'Popen', popen)
    monkeypatch.setattr(launcher.shutil, 'which', lambda _: '/usr/bin/cloudflared')
    monkeypatch.setattr(launcher, 'check_port', lambda _: None)
    assert launcher.main(['--remote', '--no-browser']) == 1
    assert popen.call_count == 1


def test_origin_exit_closes_tunnel(monkeypatch):
    server = Mock()
    server.poll.return_value = 1
    server.returncode = 1
    tunnel = Mock()
    tunnel.poll.return_value = None
    popen = Mock(side_effect=[server, tunnel])
    monkeypatch.setattr(launcher.subprocess, 'Popen', popen)
    monkeypatch.setattr(launcher.shutil, 'which', lambda _: '/usr/bin/cloudflared')
    monkeypatch.setattr(launcher, 'check_port', lambda _: None)
    monkeypatch.setattr(launcher, 'wait_ready', lambda *a: None)
    assert launcher.main(['--remote', '--no-browser']) == 1
    assert popen.call_count == 2
    assert 'PEDIT_REMOTE_PASSWORD' in popen.call_args_list[0].kwargs['env']
    assert popen.call_args_list[1].args[0][-1] == 'http://127.0.0.1:8000'
    tunnel.terminate.assert_called_once()
