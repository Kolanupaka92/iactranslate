import socketserver
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))


_FIXTURE_FILES = (
    "rvtools_sample.xlsx", "vmware_sample.csv", "hyperv_sample.csv",
    "cmdb_sample.csv", "cmdb_util_sample.csv", "cloud_sample.csv",
    "k8s_sample.json",
)


def _ensure_fixtures() -> None:
    if not all((FIXTURES / f).exists() for f in _FIXTURE_FILES):
        import make_fixtures

        make_fixtures.main()


_ensure_fixtures()


@pytest.fixture
def rvtools_path() -> str:
    return str(FIXTURES / "rvtools_sample.xlsx")


@pytest.fixture
def vmware_csv_path() -> str:
    return str(FIXTURES / "vmware_sample.csv")


@pytest.fixture
def hyperv_path() -> str:
    return str(FIXTURES / "hyperv_sample.csv")


@pytest.fixture
def cmdb_path() -> str:
    return str(FIXTURES / "cmdb_sample.csv")


@pytest.fixture
def cloud_path() -> str:
    return str(FIXTURES / "cloud_sample.csv")


@pytest.fixture
def cmdb_util_path() -> str:
    return str(FIXTURES / "cmdb_util_sample.csv")


@pytest.fixture
def k8s_path() -> str:
    return str(FIXTURES / "k8s_sample.json")


@pytest.fixture(autouse=True)
def _rate_limits_off(monkeypatch):
    """Disable rate limiting for the suite by default.

    Every test shares one process and one client address, so the limiters would
    otherwise see the whole suite as a single hammering client and fail tests
    that have nothing to do with throttling. `tests/test_ratelimit.py` opts back
    in explicitly, which is where the limits actually belong under test.
    """
    for var in ("IACTRANSLATE_RATE_AUTH", "IACTRANSLATE_RATE_WRITE", "IACTRANSLATE_RATE_READ"):
        monkeypatch.setenv(var, "0")
    from iactranslate.api.ratelimit import reset_all
    reset_all()


class _Smtp(socketserver.ThreadingTCPServer):
    """Just enough SMTP for smtplib.send_message: EHLO, MAIL, RCPT, DATA, QUIT."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        self.messages = []
        server = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.wfile.write(b"220 localhost test\r\n")
                envelope = {"rcpt": []}
                while True:
                    line = self.rfile.readline().decode()
                    if not line:
                        return
                    verb = line[:4].upper()
                    if verb in ("EHLO", "HELO"):
                        self.wfile.write(b"250 localhost\r\n")
                    elif verb == "MAIL":
                        envelope["from"] = line
                        self.wfile.write(b"250 ok\r\n")
                    elif verb == "RCPT":
                        envelope["rcpt"].append(line)
                        self.wfile.write(b"250 ok\r\n")
                    elif verb == "DATA":
                        self.wfile.write(b"354 go\r\n")
                        data = []
                        while True:
                            chunk = self.rfile.readline().decode()
                            if chunk in (".\r\n", ""):
                                break
                            data.append(chunk)
                        server.messages.append({"data": "".join(data), **envelope})
                        self.wfile.write(b"250 queued\r\n")
                    elif verb == "QUIT":
                        self.wfile.write(b"221 bye\r\n")
                        return
                    else:
                        self.wfile.write(b"502 no\r\n")

        super().__init__(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()


@pytest.fixture
def smtp_server():
    """A real SMTP listener on 127.0.0.1 that records what it is sent."""
    server = _Smtp()
    yield server
    server.shutdown()
    server.server_close()
