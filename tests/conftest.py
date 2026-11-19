"""Fixtures for SFTP integration tests."""

import os
import socket
import threading
from pathlib import Path

import paramiko
import pytest

from frc_log_uploader.config import RobotConfig


class MockSFTPInterface(paramiko.SFTPServerInterface):
    """SFTP interface that serves files from a root directory."""

    root_dir = "."

    def __init__(self, *args, **kwargs):
        self._root = self.root_dir
        super().__init__()

    def _full_path(self, path):
        """Convert a remote path to a local filesystem path."""
        clean = path.lstrip("/")
        if not clean:
            clean = "."
        return os.path.join(self._root, clean)

    def stat(self, path):
        full_path = self._full_path(path)
        st = os.stat(full_path)
        attr = paramiko.SFTPAttributes.from_stat(st)
        return attr

    def lstat(self, path):
        return self.stat(path)

    def list_folder(self, path):
        full_path = self._full_path(path)
        try:
            entries = os.listdir(full_path)
        except FileNotFoundError:
            raise paramiko.SFTPError("No such file", paramiko.SFTP_NO_SUCH_FILE)
        except NotADirectoryError:
            raise paramiko.SFTPError("Not a directory", paramiko.SFTP_FAILURE)

        attrs = []
        for name in entries:
            full = os.path.join(full_path, name)
            st = os.stat(full)
            attr = paramiko.SFTPAttributes.from_stat(st)
            attr.filename = name
            attrs.append(attr)
        return attrs

    def open(self, path, flags, attr):
        full_path = self._full_path(path)
        read = bool(flags & paramiko.SFTP_OPEN_READ)
        write = bool(flags & paramiko.SFTP_OPEN_WRITE)
        create = bool(flags & paramiko.SFTP_OPEN_CREATE)
        truncate = bool(flags & paramiko.SFTP_OPEN_TRUNCATE)

        mode = "r"
        if write and read:
            mode = "r+"
        elif write:
            mode = "w" if truncate else "a"
        if create and not os.path.exists(full_path):
            Path(full_path).touch()

        try:
            if mode == "r":
                f = open(full_path, "rb")
            elif mode == "r+":
                f = open(full_path, "r+b")
            elif mode == "w":
                f = open(full_path, "wb")
            else:
                f = open(full_path, "ab")

            handle = paramiko.SFTPHandle()
            handle.filename = full_path
            handle.fileobj = f
            handle.read_offset = 0
            handle.write_offset = 0
            return handle
        except FileNotFoundError:
            raise paramiko.SFTPError("No such file", paramiko.SFTP_NO_SUCH_FILE)

    def remove(self, path):
        full_path = self._full_path(path)
        try:
            os.remove(full_path)
        except FileNotFoundError:
            raise paramiko.SFTPError("No such file", paramiko.SFTP_NO_SUCH_FILE)

    def rename(self, oldpath, newpath):
        full_old = self._full_path(oldpath)
        full_new = self._full_path(newpath)
        try:
            os.rename(full_old, full_new)
        except FileNotFoundError:
            raise paramiko.SFTPError("No such file", paramiko.SFTP_NO_SUCH_FILE)

    def mkdir(self, path, attr):
        full_path = self._full_path(path)
        try:
            os.mkdir(full_path)
        except FileExistsError:
            raise paramiko.SFTPError("File exists", paramiko.SFTP_FAILURE)

    def rmdir(self, path):
        full_path = self._full_path(path)
        try:
            os.rmdir(full_path)
        except FileNotFoundError:
            raise paramiko.SFTPError("No such file", paramiko.SFTP_NO_SUCH_FILE)

    def chattr(self, path, attr):
        return paramiko.SFTP_OK


class MockSSHServer(paramiko.ServerInterface):
    """SSH server interface for authentication and channel requests."""

    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_auth_password(self, username, password):
        if username == "lvuser" and password == "":
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_subsystem_request(self, channel, name):
        if name == "sftp":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_exec_request(self, channel, command):
        return True


@pytest.fixture
def sftp_server_root(tmp_path):
    """Create a temporary directory with test log files."""
    root = tmp_path / "sftp_root"
    root.mkdir()

    (root / "match1.wpilog").write_bytes(b"A" * 1024)
    (root / "match2.wpilog").write_bytes(b"B" * 2048)
    (root / "match3.wpilog").write_bytes(b"C" * 4096)

    subdir = root / "subdir"
    subdir.mkdir()
    (subdir / "signal.hoot").write_bytes(b"D" * 8192)

    (root / "unstable.wpilog").write_bytes(b"E" * 512)

    return root


@pytest.fixture
def sftp_server(sftp_server_root):
    """Start a real SFTP server on a random port and yield its connection info."""
    host_key = paramiko.RSAKey.generate(2048)
    host_key_path = str(sftp_server_root / "host_key")
    host_key.write_private_key_file(host_key_path)

    ssh_server = MockSSHServer()

    # Create a listening socket
    listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen_sock.bind(("127.0.0.1", 0))
    listen_sock.listen(5)
    port = listen_sock.getsockname()[1]

    server_ready = threading.Event()
    server_stop = threading.Event()
    transport_holder = {}
    accept_thread_holder = {}

    def accept_loop():
        """Accept connections in a loop until stop is requested."""
        listen_sock.settimeout(0.5)
        while not server_stop.is_set():
            try:
                client_sock, _ = listen_sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break

            transport = paramiko.Transport(client_sock)
            transport.add_server_key(paramiko.RSAKey(filename=host_key_path))
            # Set root_dir on the interface class before creating handler
            MockSFTPInterface.root_dir = str(sftp_server_root)
            transport.set_subsystem_handler(
                "sftp",
                paramiko.SFTPServer,
                "sftp",
                ssh_server,
                sftp_si=MockSFTPInterface,
            )

            def run_transport():
                try:
                    transport.start_server(server=ssh_server)
                except Exception:
                    try:
                        transport.close()
                    except Exception:
                        pass

            t = threading.Thread(target=run_transport, daemon=True)
            t.start()
            accept_thread_holder["thread"] = t
            transport_holder["transport"] = transport
            server_ready.set()

    thread = threading.Thread(target=accept_loop, daemon=True)
    thread.start()

    # Wait for server to be ready to accept
    server_ready.wait(timeout=10)

    yield {
        "host": "127.0.0.1",
        "port": port,
        "username": "lvuser",
        "password": "",
        "root_dir": str(sftp_server_root),
    }

    server_stop.set()
    try:
        listen_sock.close()
    except Exception:
        pass
    try:
        transport = transport_holder.get("transport")
        if transport:
            transport.close()
    except Exception:
        pass


@pytest.fixture
def sftp_config(sftp_server):
    """Create a RobotConfig pointing to the test SFTP server."""
    return RobotConfig(
        address=sftp_server["host"],
        port=sftp_server["port"],
        username=sftp_server["username"],
        password=sftp_server["password"],
        log_directory=sftp_server["root_dir"],
        connection_timeout=5.0,
        transfer_timeout=10.0,
    )
