import socket

import pytest


def test_raw_socket_connections_are_blocked():
    with pytest.raises(RuntimeError, match="network access is blocked"):
        socket.create_connection(("cad.onshape.com", 443), timeout=1)
    with pytest.raises(RuntimeError, match="network access is blocked"):
        socket.getaddrinfo("api.trello.com", 443)


def test_http_libraries_are_blocked():
    requests = pytest.importorskip("requests")
    with pytest.raises(Exception, match="network access is blocked"):
        requests.get("https://cad.onshape.com/api/v10/documents", timeout=1)
