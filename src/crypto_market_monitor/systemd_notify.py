"""Optional systemd progress notification shared by the standalone monitor."""

import os
import socket


def notify(message: str) -> None:
    address = os.environ.get("NOTIFY_SOCKET")
    if address and hasattr(socket, "AF_UNIX"):
        if address.startswith("@"):
            address = "\0" + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
            client.sendto(message.encode(), address)
