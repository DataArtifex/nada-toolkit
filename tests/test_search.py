import json
from unittest.mock import MagicMock

from typer.testing import CliRunner

from dartfx.nada.cli import app
from dartfx.nada.nada import NadaServer, SearchParameters


def test_search_parameters_model() -> None:
    params = SearchParameters(sk="household", country="ID", ps=20, page=2)
    dumped = params.model_dump(by_alias=True, exclude_none=True)
    assert dumped["sk"] == "household"
    assert dumped["country"] == "ID"
    assert dumped["ps"] == 20
    assert dumped["page"] == 2


def test_search_execution(test_server: NadaServer) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(
        return_value={
            "result": {
                "total": 1,
                "rows": [
                    {
                        "id": 101,
                        "idno": "SURVEY-2026",
                        "title": "Household Income Survey",
                        "nation": "United States",
                    }
                ],
            }
        }
    )
    test_server.session.request = MagicMock(return_value=mock_resp)

    res = test_server.search_simple("household")
    assert "result" in res
    assert res["result"]["total"] == 1
    assert res["result"]["rows"][0]["idno"] == "SURVEY-2026"


def test_cli_search_command(monkeypatch) -> None:
    mock_results = {
        "result": {
            "total": 1,
            "rows": [
                {
                    "id": 1,
                    "idno": "WB-001",
                    "title": "Living Standards Measurement",
                    "nation": "Malawi",
                    "created": "2026-01-01",
                }
            ],
        }
    }

    class MockServer:
        installation = MagicMock(clean_hostname="microdata.worldbank.org")

        def search(self, _params):
            return mock_results

    monkeypatch.setattr("dartfx.nada.cli.get_server", lambda *_args, **_kwargs: MockServer())

    runner = CliRunner()
    result_table = runner.invoke(app, ["search", "living", "--limit", "1"])
    assert result_table.exit_code == 0
    assert "Living Standards" in result_table.stdout
    assert "WB-001" in result_table.stdout

    result_csv = runner.invoke(app, ["search", "living", "--format", "csv"])
    assert result_csv.exit_code == 0
    assert "WB-001" in result_csv.stdout
    assert "Malawi" in result_csv.stdout

    result_json = runner.invoke(app, ["search", "living", "--format", "json"])
    assert result_json.exit_code == 0
    data = json.loads(result_json.stdout)
    assert data["result"]["total"] == 1
