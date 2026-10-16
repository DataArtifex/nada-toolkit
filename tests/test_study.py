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


def test_get_study_croissant(test_server: NadaServer) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(return_value={"@context": {"@vocab": "https://schema.org/"}, "name": "Croissant Study"})
    test_server.session.request = MagicMock(return_value=mock_resp)

    res = test_server.get_study_croissant("MWI-2026")
    assert isinstance(res, dict)
    assert res["name"] == "Croissant Study"


def test_get_study_schema(test_server: NadaServer) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = """
    <html><head>
    <script type="application/ld+json">
    {
      "@context": "https://schema.org/",
      "@type": "Dataset",
      "name": "Schema Study",
      "identifier": "MWI-2026"
    }
    </script>
    </head></html>
    """
    test_server.session.request = MagicMock(return_value=mock_resp)

    res = test_server.get_study_schema("MWI-2026")
    assert isinstance(res, dict)
    assert res["@context"] == "https://schema.org/"
    assert res["name"] == "Schema Study"


def test_cli_study_command(monkeypatch) -> None:
    class MockServer:
        def get_study(self, identifier):
            return {"idno": identifier, "title": "Sample Study"}

        def get_study_ddi(self, _identifier):
            return "<codeBook>XML Content</codeBook>"

        def get_study_croissant(self, _identifier):
            return {"@context": "https://schema.org", "name": "ML Dataset"}

        def get_study_schema(self, _identifier):
            return {"@context": "https://schema.org/", "name": "Schema Dataset"}

    monkeypatch.setattr("dartfx.nada.cli.get_server", lambda *_args, **_kwargs: MockServer())

    runner = CliRunner()
    res_study = runner.invoke(app, ["study", "MWI-2026"])
    assert res_study.exit_code == 0
    assert "Sample Study" in res_study.stdout

    res_ddi = runner.invoke(app, ["study", "MWI-2026", "--export", "ddi"])
    assert res_ddi.exit_code == 0
    assert "<codeBook>" in res_ddi.stdout

    res_croissant = runner.invoke(app, ["study", "MWI-2026", "--export", "croissant"])
    assert res_croissant.exit_code == 0
    assert "ML Dataset" in res_croissant.stdout

    res_schema = runner.invoke(app, ["study", "MWI-2026", "--export", "schema"])
    assert res_schema.exit_code == 0
    assert "Schema Dataset" in res_schema.stdout


def test_cli_info_command(monkeypatch) -> None:
    from dartfx.nada.nada import ServerInstallation

    class MockServer:
        installation = ServerInstallation(
            name="IHSN Catalog",
            clean_hostname="catalog.ihsn.org",
            country="Global",
            country_code="GL",
            url="https://catalog.ihsn.org",
        )
        url = "https://catalog.ihsn.org"

        def detect_server_version(self):
            return 5

        def get_collections(self):
            return [{"id": 1, "title": "Main"}]

        def search(self, _params):
            return {"result": {"total": 13085}}

    monkeypatch.setattr("dartfx.nada.cli.get_server", lambda *_args, **_kwargs: MockServer())
    monkeypatch.setattr(
        "dartfx.nada.harvester.fetch_server_stats",
        lambda *_args, **_kwargs: {
            "is_nada": True,
            "studies": 13085,
            "collections": 1,
            "version": "5",
        },
    )

    runner = CliRunner()
    res_info = runner.invoke(app, ["info", "catalog.ihsn.org"])
    assert res_info.exit_code == 0
    assert "catalog.ihsn.org" in res_info.stdout
    assert "13,085" in res_info.stdout
    assert "Croissant" in res_info.stdout
    assert "Schema.org" in res_info.stdout
