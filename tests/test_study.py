from unittest.mock import MagicMock

from typer.testing import CliRunner

from dartfx.nada.cli import app
from dartfx.nada.nada import NadaServer


def test_get_study(test_server: NadaServer) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(return_value={"id": 1, "idno": "MWI-2026", "title": "Malawi Survey"})
    test_server.session.request = MagicMock(return_value=mock_resp)

    res = test_server.get_study("MWI-2026")
    assert res["idno"] == "MWI-2026"
    assert res["title"] == "Malawi Survey"


def test_get_study_ddi(test_server: NadaServer) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "<codeBook version='2.5'><docDscr><titl>Test DDI</titl></docDscr></codeBook>"
    test_server.session.request = MagicMock(return_value=mock_resp)

    xml_text = test_server.get_study_ddi("MWI-2026")
    assert "<codeBook" in xml_text
    assert "Test DDI" in xml_text


def test_get_study_resources(test_server: NadaServer) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(return_value=[{"resource_id": "r1", "title": "Questionnaire"}])
    test_server.session.request = MagicMock(return_value=mock_resp)

    res = test_server.get_study_resources("MWI-2026")
    assert isinstance(res, list)
    assert res[0]["title"] == "Questionnaire"


def test_cli_study_command(monkeypatch) -> None:
    class MockServer:
        def get_study(self, identifier):
            return {"idno": identifier, "title": "Sample Study"}

        def get_study_ddi(self, _identifier):
            return "<codeBook>XML Content</codeBook>"

    monkeypatch.setattr("dartfx.nada.cli.get_server", lambda *_args, **_kwargs: MockServer())

    runner = CliRunner()
    res_study = runner.invoke(app, ["study", "MWI-2026"])
    assert res_study.exit_code == 0
    assert "Sample Study" in res_study.stdout

    res_ddi = runner.invoke(app, ["study", "MWI-2026", "--export", "ddi"])
    assert res_ddi.exit_code == 0
    assert "<codeBook>" in res_ddi.stdout
