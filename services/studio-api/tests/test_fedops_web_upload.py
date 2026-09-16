from pathlib import Path

from studio_api.integrations import fedops_web


class _Response:
    status = 201

    def read(self) -> bytes:
        return b'{"releaseId":"release-test"}'


class _Connection:
    def __init__(self, *, fail_send: bool = False) -> None:
        self.fail_send = fail_send
        self.closed = False

    def putrequest(self, _method: str, _target: str) -> None:
        return None

    def putheader(self, _name: str, _value: str) -> None:
        return None

    def endheaders(self) -> None:
        return None

    def send(self, _chunk: bytes) -> None:
        if self.fail_send:
            raise BrokenPipeError("proxy reset")

    def getresponse(self) -> _Response:
        return _Response()

    def close(self) -> None:
        self.closed = True


def test_binary_upload_retries_one_transport_reset(tmp_path: Path, monkeypatch) -> None:
    artifact = tmp_path / "release.fedops.zip"
    artifact.write_bytes(b"release")
    connections = [_Connection(fail_send=True), _Connection()]
    monkeypatch.setattr(fedops_web, "fedops_api_url", lambda path: f"https://fedops.test/{path}")
    monkeypatch.setattr(fedops_web, "_connection", lambda _url: (connections.pop(0), "/upload"))
    monkeypatch.setattr(fedops_web.time, "sleep", lambda _seconds: None)

    result = fedops_web.upload_fedops_binary(
        {},
        "task-releases/tasks/task/releases",
        artifact,
        content_type="application/octet-stream",
    )

    assert result == {"releaseId": "release-test"}
    assert connections == []


def test_binary_upload_reports_transport_cause_after_retry(tmp_path: Path, monkeypatch) -> None:
    artifact = tmp_path / "release.fedops.zip"
    artifact.write_bytes(b"release")
    connections = [_Connection(fail_send=True), _Connection(fail_send=True)]
    monkeypatch.setattr(fedops_web, "fedops_api_url", lambda path: f"https://fedops.test/{path}")
    monkeypatch.setattr(fedops_web, "_connection", lambda _url: (connections.pop(0), "/upload"))
    monkeypatch.setattr(fedops_web.time, "sleep", lambda _seconds: None)

    try:
        fedops_web.upload_fedops_binary(
            {},
            "task-releases/tasks/task/releases",
            artifact,
            content_type="application/octet-stream",
        )
    except RuntimeError as error:
        assert "BrokenPipeError: proxy reset" in str(error)
    else:
        raise AssertionError("A repeated transport reset must fail with its concrete cause.")
