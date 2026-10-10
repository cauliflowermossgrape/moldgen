"""Packaged-app launcher: picks a usable port, delegates arguments to the CLI."""

import socket

import pytest

from moldgen import desktop


def test_free_port_is_bindable_and_skips_busy_ports():
    port = desktop.free_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((desktop.HOST, port))  # nothing else holds it
        busy = port
        # with that port taken, the next call must pick another one
        assert desktop.free_port() != busy


def test_arguments_go_to_the_cli(capsys):
    with pytest.raises(SystemExit) as exc:
        desktop.main(["--version"])
    assert exc.value.code == 0
    assert "moldgen" in capsys.readouterr().out
