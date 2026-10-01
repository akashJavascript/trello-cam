"""Test-wide guard: no test may touch the network.

The Onshape budget is shared across the whole enterprise, so tests use recorded fixtures and
fakes only. This fixture makes any connection attempt fail loudly instead of quietly spending calls.
"""

import socket

import pytest


class NetworkBlockedError(RuntimeError):
    pass


def _blocked(*args, **kwargs):
    raise NetworkBlockedError(
        "network access is blocked in tests: use recorded fixtures or fakes, never Onshape or Trello")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket.socket, "sendto", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
