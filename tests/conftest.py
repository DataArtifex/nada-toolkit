from pathlib import Path

import pytest
from dotenv import load_dotenv

from dartfx.nada.nada import NadaServer, ServerInstallation


@pytest.fixture(scope="session", autouse=True)
def load_env() -> None:
    dotenv_path = Path(__file__).parent / "../.env"
    load_dotenv(dotenv_path=dotenv_path)


@pytest.fixture(scope="session")
def test_server() -> NadaServer:
    inst = ServerInstallation(hostname="microdata.worldbank.org", name="World Bank Microdata Library")
    return NadaServer(server=inst, lookup_installation=False)
