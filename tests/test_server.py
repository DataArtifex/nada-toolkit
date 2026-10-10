from unittest.mock import MagicMock

import pytest

from dartfx.nada.nada import (
    NadaApiError,
    NadaServer,
)


def test_nada_server_initialization() -> None:
    server = NadaServer("microdata.worldbank.org", lookup_installation=False)
    assert server.installation.clean_hostname == "microdata.worldbank.org"
    assert server.url == "https://microdata.worldbank.org"


def test_nada_server_strip_https() -> None:
    server = NadaServer("https://nada.ubos.org/", lookup_installation=False)
    assert server.installation.clean_hostname == "nada.ubos.org"
    assert server.url == "https://nada.ubos.org"


def test_nada_server_env_var(monkeypatch) -> None:
    monkeypatch.setenv("NADA_SERVER", "interactive.statssa.gov.za")
    server = NadaServer(lookup_installation=False)
    assert server.installation.clean_hostname == "interactive.statssa.gov.za"


def test_nada_server_api_error_raise() -> None:
    server = NadaServer("mock.nada.org", lookup_installation=False, on_api_error="raise")
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.text = "Not Found"
    server.session.request = MagicMock(return_value=mock_resp)

    with pytest.raises(NadaApiError) as exc_info:
        server.get_study("non-existent-123")
    assert exc_info.value.status_code == 404


def test_nada_server_api_error_suppress() -> None:
    server = NadaServer("mock.nada.org", lookup_installation=False, on_api_error="none")
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    server.session.request = MagicMock(return_value=mock_resp)

    res = server.get_study("test-study")
    assert res is None


def test_version_detection_v5() -> None:
    server = NadaServer("mock.nada.org", lookup_installation=False)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(return_value={"result": {"rows": []}})
    server.session.get = MagicMock(return_value=mock_resp)

    ver = server.detect_server_version()
    assert ver == 5


def test_version_detection_v4() -> None:
    server = NadaServer("mock.nada.org", lookup_installation=False)

    def mock_get(url, **_kwargs):
        resp = MagicMock()
        if "api/catalog/search" in url:
            resp.status_code = 404
        elif "catalog/export/csv" in url:
            resp.status_code = 200
            resp.text = "id,surveyid,title\n1,TEST,Title"
        return resp

    server.session.get = MagicMock(side_effect=mock_get)
    ver = server.detect_server_version()
    assert ver == 4
