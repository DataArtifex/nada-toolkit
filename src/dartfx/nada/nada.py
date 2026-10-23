import csv
import importlib.resources
import inspect
import io
import json
import logging
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any, Literal

import requests
import requests_cache
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .__about__ import __version__

# Raw Country Name to ISO 3166-1 Alpha-2 Code Crosswalk (Alphabetical)
COUNTRY_TO_ISO2: dict[str, str] = {
    "AFGHANISTAN": "AF",
    "ALBANIA": "AL",
    "ALGERIA": "DZ",
    "ANDORRA": "AD",
    "ANGOLA": "AO",
    "ARGENTINA": "AR",
    "ARMENIA": "AM",
    "AUSTRALIA": "AU",
    "AUSTRIA": "AT",
    "AZERBAIJAN": "AZ",
    "BAHAMAS": "BS",
    "BAHRAIN": "BH",
    "BANGLADESH": "BD",
    "BARBADOS": "BB",
    "BELARUS": "BY",
    "BELGIUM": "BE",
    "BELIZE": "BZ",
    "BENIN": "BJ",
    "BHUTAN": "BT",
    "BOLIVIA": "BO",
    "BOSNIA AND HERZEGOVINA": "BA",
    "BOTSWANA": "BW",
    "BRAZIL": "BR",
    "BRUNEI": "BN",
    "BULGARIA": "BG",
    "BURKINA FASO": "BF",
    "BURUNDI": "BI",
    "CABO VERDE": "CV",
    "CAMBODIA": "KH",
    "CAMEROON": "CM",
    "CANADA": "CA",
    "CAPE VERDE": "CV",
    "CENTRAL AFRICAN REPUBLIC": "CF",
    "CHAD": "TD",
    "CHILE": "CL",
    "CHINA": "CN",
    "COLOMBIA": "CO",
    "COMOROS": "KM",
    "CONGO": "CG",
    "COSTA RICA": "CR",
    "CROATIA": "HR",
    "CUBA": "CU",
    "CYPRUS": "CY",
    "CZECH REPUBLIC": "CZ",
    "CZECHIA": "CZ",
    "DEMOCRATIC REPUBLIC OF THE CONGO": "CD",
    "DENMARK": "DK",
    "DEUTSCHLAND": "DE",
    "DJIBOUTI": "DJ",
    "DOMINICA": "DM",
    "DOMINICAN REPUBLIC": "DO",
    "ECUADOR": "EC",
    "EGYPT": "EG",
    "EL SALVADOR": "SV",
    "EQUATORIAL GUINEA": "GQ",
    "ERITREA": "ER",
    "ESTONIA": "EE",
    "ESWATINI": "SZ",
    "ETHIOPIA": "ET",
    "FIJI": "FJ",
    "FINLAND": "FI",
    "FRANCE": "FR",
    "GABON": "GA",
    "GAMBIA": "GM",
    "GEORGIA": "GE",
    "GERMANY": "DE",
    "GHANA": "GH",
    "GREAT BRITAIN": "GB",
    "GREECE": "GR",
    "GRENADA": "GD",
    "GUATEMALA": "GT",
    "GUINEA": "GN",
    "GUINEA-BISSAU": "GW",
    "GUINEA BISSAU": "GW",
    "GUYANA": "GY",
    "HAITI": "HT",
    "HOLLAND": "NL",
    "HONDURAS": "HN",
    "HONG KONG": "HK",
    "HUNGARY": "HU",
    "ICELAND": "IS",
    "INDIA": "IN",
    "INDONESIA": "ID",
    "IRAN": "IR",
    "IRAQ": "IQ",
    "IRELAND": "IE",
    "ISRAEL": "IL",
    "ITALY": "IT",
    "IVORY COAST": "CI",
    "CÔTE D'IVOIRE": "CI",
    "COTE D'IVOIRE": "CI",
    "JAMAICA": "JM",
    "JAPAN": "JP",
    "JORDAN": "JO",
    "KAZAKHSTAN": "KZ",
    "KENYA": "KE",
    "KIRIBATI": "KI",
    "KOREA": "KR",
    "KUWAIT": "KW",
    "KYRGYZSTAN": "KG",
    "LAOS": "LA",
    "LATVIA": "LV",
    "LEBANON": "LB",
    "LESOTHO": "LS",
    "LIBERIA": "LR",
    "LIBYA": "LY",
    "LIECHTENSTEIN": "LI",
    "LITHUANIA": "LT",
    "LUXEMBOURG": "LU",
    "MADAGASCAR": "MG",
    "MALAWI": "MW",
    "MALAYSIA": "MY",
    "MALDIVES": "MV",
    "MALI": "ML",
    "MALTA": "MT",
    "MARSHALL ISLANDS": "MH",
    "MAURITANIA": "MR",
    "MAURITIUS": "MU",
    "MEXICO": "MX",
    "MICRONESIA": "FM",
    "MOLDOVA": "MD",
    "MONACO": "MC",
    "MONGOLIA": "MN",
    "MONTENEGRO": "ME",
    "MOROCCO": "MA",
    "MOZAMBIQUE": "MZ",
    "MYANMAR": "MM",
    "NAMIBIA": "NA",
    "NAURU": "NR",
    "NEPAL": "NP",
    "NETHERLANDS": "NL",
    "NEW ZEALAND": "NZ",
    "NICARAGUA": "NI",
    "NIGER": "NE",
    "NIGERIA": "NG",
    "NORTH KOREA": "KP",
    "NORTH MACEDONIA": "MK",
    "NORWAY": "NO",
    "OMAN": "OM",
    "PAKISTAN": "PK",
    "PALAU": "PW",
    "PALESTINE": "PS",
    "PANAMA": "PA",
    "PAPUA NEW GUINEA": "PG",
    "PARAGUAY": "PY",
    "PERU": "PE",
    "PHILIPPINES": "PH",
    "PHILLIPINES": "PH",
    "POLAND": "PL",
    "PORTUGAL": "PT",
    "QATAR": "QA",
    "ROMANIA": "RO",
    "RUSSIA": "RU",
    "RWANDA": "RW",
    "SAMOA": "WS",
    "SAN MARINO": "SM",
    "SAO TOME AND PRINCIPE": "ST",
    "SAUDI ARABIA": "SA",
    "SENEGAL": "SN",
    "SERBIA": "RS",
    "SEYCHELLES": "SC",
    "SIERRA LEONE": "SL",
    "SINGAPORE": "SG",
    "SLOVAKIA": "SK",
    "SLOVENIA": "SI",
    "SOLOMON ISLANDS": "SB",
    "SOMALIA": "SO",
    "SOUTH AFRICA": "ZA",
    "SOUTH KOREA": "KR",
    "SOUTH SUDAN": "SS",
    "SPAIN": "ES",
    "SRI LANKA": "LK",
    "SUDAN": "SD",
    "SURINAME": "SR",
    "SWEDEN": "SE",
    "SWITZERLAND": "CH",
    "SYRIA": "SY",
    "TAIWAN": "TW",
    "TAIWAN (ROC)": "TW",
    "TAJIKISTAN": "TJ",
    "TANZANIA": "TZ",
    "THAILAND": "TH",
    "TIMOR-LESTE": "TL",
    "TOGO": "TG",
    "TONGA": "TO",
    "TRINIDAD AND TOBAGO": "TT",
    "TUNISIA": "TN",
    "TURKEY": "TR",
    "TURKMENISTAN": "TM",
    "TUVALU": "TV",
    "TÜRKIYE": "TR",
    "UGANDA": "UG",
    "UK": "GB",
    "UKRAINE": "UA",
    "UNITED ARAB EMIRATES": "AE",
    "UNITED KINGDOM": "GB",
    "UNITED STATES": "US",
    "UNITED STATES OF AMERICA": "US",
    "URUGUAY": "UY",
    "USA": "US",
    "UZBEKISTAN": "UZ",
    "VANUATU": "VU",
    "VATICAN CITY": "VA",
    "VENEZUELA": "VE",
    "VIETNAM": "VN",
    "YEMEN": "YE",
    "ZAMBIA": "ZM",
    "ZIMBABWE": "ZW",
}

# Reverse ISO 3166-1 Alpha-2 to Canonical Country Name mapping
ISO2_TO_COUNTRY: dict[str, str] = {
    code: name.title() for name, code in COUNTRY_TO_ISO2.items() if name not in ("USA", "UK")
}
ISO2_TO_COUNTRY.update(
    {
        "US": "United States",
        "GB": "United Kingdom",
        "CI": "Côte d'Ivoire",
        "CD": "DR Congo",
        "LA": "Laos",
        "FM": "Micronesia",
        "PS": "Palestine",
        "RU": "Russia",
        "KR": "South Korea",
        "KP": "North Korea",
        "SY": "Syria",
        "TZ": "Tanzania",
        "VN": "Vietnam",
        "VG": "British Virgin Islands",
        "VI": "U.S. Virgin Islands",
    }
)


def get_iso2_code(raw_country: str, existing_code: str = "") -> str:
    """Resolve raw country string to 2-letter ISO 3166-1 Alpha-2 code."""
    if existing_code and len(existing_code.strip()) == 2:
        return existing_code.strip().upper()

    clean_country = raw_country.strip().upper()
    if clean_country in COUNTRY_TO_ISO2:
        return COUNTRY_TO_ISO2[clean_country]

    if "," in clean_country:
        first_part = clean_country.split(",")[0].strip()
        if first_part in COUNTRY_TO_ISO2:
            return COUNTRY_TO_ISO2[first_part]

    if len(clean_country) == 2:
        return clean_country

    return ""


def matches_country(target_filter: str, raw_country: str, existing_code: str = "") -> bool:
    """Check if user filter matches 2-letter ISO country code or raw country name."""
    tf = target_filter.strip().upper()
    server_iso2 = get_iso2_code(raw_country, existing_code)

    # 1. If target filter is a 2-letter code, strictly match server ISO2 code only
    if len(tf) == 2:
        return tf == server_iso2

    # 2. Compare target filter resolved ISO2 vs server ISO2
    filter_iso2 = COUNTRY_TO_ISO2.get(tf)
    if filter_iso2 and server_iso2 and filter_iso2 == server_iso2:
        return True

    # 3. Fallback substring matching on raw country string ONLY for filters longer than 2 characters
    tf_low = target_filter.strip().lower()
    if len(tf_low) > 2 and tf_low in raw_country.lower():
        return True

    return False


class Custodian(BaseModel):
    """Organization custodian responsible for the NADA catalog."""

    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    url: str | None = None
    ror: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_custodian(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            if "ror_uri" in data and "ror" not in data:
                data["ror"] = data.pop("ror_uri")
            if "home_page" in data and "url" not in data:
                data["url"] = data.pop("home_page")
        return data

    @field_validator("ror", mode="before")
    @classmethod
    def _normalize_ror(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return None
            if "ror.org/" in v:
                return v.rstrip("/").split("ror.org/")[-1].strip()
            return v.rstrip("/")
        return v

    @property
    def ror_url(self) -> str | None:
        """Compose the full ROR URL from the identifier."""
        return f"https://ror.org/{self.ror}" if self.ror else None

    @property
    def ror_uri(self) -> str | None:
        """Backwards compatibility alias for ror_url."""
        return self.ror_url


class GeoCoverage(BaseModel):
    """Geographical scope and coverage details."""

    model_config = ConfigDict(extra="ignore")

    level: Literal["global", "regional", "national", "subnational", "city", "other"] | str | None = None
    description: str | None = None
    geonames: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_geonames(cls, data: Any) -> Any:
        if isinstance(data, dict):
            gn = data.get("geonames")
            if gn is None:
                gn = data.get("geonames_uri") or data.get("geonames_uris")
            if isinstance(gn, str):
                data["geonames"] = [gn] if gn.strip() else []
            elif isinstance(gn, list):
                data["geonames"] = [str(x) for x in gn if x]
            else:
                data["geonames"] = []
        return data

    @property
    def geonames_uri(self) -> str | None:
        """Backwards compatibility property returning the primary GeoNames URI."""
        return self.geonames[0] if self.geonames else None


class ServerInstallation(BaseModel):
    """Represents a NADA installation with metadata and helper properties."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str | None = None
    description: str | None = None
    hostname: str | None = None
    catalog_url: str | None = Field(default=None, alias="url")
    country: str | None = None
    country_code: str | None = None
    continent: str | None = None
    version: int | str | None = None
    status: str | None = None
    collections_count: int | None = None
    studies_count: int | None = None
    contact_email: str | None = None
    about_url: str | None = None
    id: str | None = None
    scheme: str = "https"
    custodian: Custodian | None = None
    geocoverage: GeoCoverage | None = None
    domains: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            url = data.get("url") or data.get("home")
            if url and not data.get("hostname"):
                parsed_host = urllib.parse.urlparse(str(url)).hostname
                data["hostname"] = parsed_host or str(url)
            if url and str(url).lower().startswith("http://") and "scheme" not in data:
                data["scheme"] = "http"
            # Normalize custodian dict fields (home_page -> url, ror_uri -> ror)
            cust = data.get("custodian")
            if isinstance(cust, dict):
                norm_cust = dict(cust)
                if "home_page" in norm_cust and "url" not in norm_cust:
                    norm_cust["url"] = norm_cust.pop("home_page")
                if "ror_uri" in norm_cust and "ror" not in norm_cust:
                    norm_cust["ror"] = norm_cust.pop("ror_uri")
                data["custodian"] = norm_cust
            elif isinstance(cust, str):
                if cust.startswith("http://") or cust.startswith("https://"):
                    data["custodian"] = {"url": cust}
                else:
                    data["custodian"] = {"name": cust}
            # Normalize topics to domains
            if "topics" in data and not data.get("domains"):
                data["domains"] = data.pop("topics")
            # Normalize string geocoverage
            geo = data.get("geocoverage")
            if isinstance(geo, str):
                data["geocoverage"] = {"description": geo}
        return data

    def model_post_init(self, __context: Any) -> None:
        """Derive country_code, country, clean hostname, and scheme if not provided."""
        if not self.country_code and self.country:
            self.country_code = get_iso2_code(self.country)
        elif not self.country and self.country_code:
            self.country = ISO2_TO_COUNTRY.get(self.country_code.strip().upper())

        if self.catalog_url and self.catalog_url.lower().startswith("http://"):
            self.scheme = "http"

        if self.hostname:
            if self.hostname.lower().startswith("http://"):
                self.scheme = "http"
            clean = self.hostname.replace("https://", "").replace("http://", "").strip("/")
            # If path was included in hostname, separate or clean
            if "/" in clean:
                clean = clean.split("/", 1)[0]
            self.hostname = clean
        elif self.about_url and self.about_url.lower().startswith("http://"):
            self.scheme = "http"
        if not self.id and self.clean_hostname:
            self.id = self.clean_hostname

    @property
    def clean_hostname(self) -> str:
        """Return cleaned hostname without protocol, port or trailing slash."""
        if not self.hostname:
            return ""
        clean = self.hostname.replace("https://", "").replace("http://", "").strip("/")
        if "/" in clean:
            clean = clean.split("/", 1)[0]
        return clean

    @property
    def url(self) -> str:
        """Return URL for the installation."""
        if self.catalog_url:
            return self.catalog_url.rstrip("/")
        if not self.clean_hostname:
            return ""
        return f"{self.scheme}://{self.clean_hostname}"


NADA_DIRECTORY_URLS: list[str] = [
    "https://raw.githubusercontent.com/DataArtifex/nada-toolkit/main/data/servers.yaml",
]

DEFAULT_NADA_INSTALLATIONS: list[dict[str, Any]] = [
    {
        "name": "World Bank Microdata Library",
        "hostname": "microdata.worldbank.org",
        "country": "United States",
        "country_code": "US",
        "continent": "North America",
        "version": 5,
        "description": "The World Bank Microdata Library provides access to collections of research data.",
        "about_url": "https://microdata.worldbank.org",
    },
    {
        "name": "ILO Central Microdata Repository",
        "hostname": "www.ilo.org/surveydata",
        "country": "Switzerland",
        "country_code": "CH",
        "continent": "Europe",
        "version": 5,
        "description": "International Labour Organization surveys and microdata catalogue.",
        "about_url": "https://www.ilo.org/surveydata",
    },
    {
        "name": "UNHCR Microdata Library",
        "hostname": "microdata.unhcr.org",
        "country": "Switzerland",
        "country_code": "CH",
        "continent": "Europe",
        "version": 5,
        "description": "United Nations High Commissioner for Refugees microdata library.",
        "about_url": "https://microdata.unhcr.org",
    },
    {
        "name": "Pacific Data Hub Microdata Library",
        "hostname": "microdata.pacificdata.org",
        "country": "New Caledonia",
        "country_code": "NC",
        "continent": "Oceania",
        "version": 5,
        "description": "Regional microdata catalog for the Pacific Community.",
        "about_url": "https://microdata.pacificdata.org",
    },
    {
        "name": "Statistics South Africa (Stats SA)",
        "hostname": "interactive.statssa.gov.za",
        "country": "South Africa",
        "country_code": "ZA",
        "continent": "Africa",
        "version": 5,
        "description": "Stats SA National Data Archive.",
        "about_url": "https://interactive.statssa.gov.za",
    },
    {
        "name": "BPS Statistics Indonesia (Sirusa)",
        "hostname": "sirusa.bps.go.id",
        "country": "Indonesia",
        "country_code": "ID",
        "continent": "Asia",
        "version": 5,
        "description": "Badan Pusat Statistik microdata catalog.",
        "about_url": "https://sirusa.bps.go.id",
    },
    {
        "name": "National Institute of Statistics of Rwanda (NISR)",
        "hostname": "microdata.statistics.gov.rw",
        "country": "Rwanda",
        "country_code": "RW",
        "continent": "Africa",
        "version": 5,
        "description": "NISR National Data Archive.",
        "about_url": "https://microdata.statistics.gov.rw",
    },
    {
        "name": "Uganda Bureau of Statistics (UBOS)",
        "hostname": "nada.ubos.org",
        "country": "Uganda",
        "country_code": "UG",
        "continent": "Africa",
        "version": 5,
        "description": "UBOS National Data Archive.",
        "about_url": "https://nada.ubos.org",
    },
    {
        "name": "ANSD Senegal National Data Archive",
        "hostname": "anads.ansd.sn",
        "country": "Senegal",
        "country_code": "SN",
        "continent": "Africa",
        "version": 5,
        "description": "Agence Nationale de la Statistique et de la Démographie du Sénégal.",
        "about_url": "https://anads.ansd.sn",
    },
    {
        "name": "INEI Microdatos Peru",
        "hostname": "iinei.inei.gob.pe",
        "country": "Peru",
        "country_code": "PE",
        "continent": "South America",
        "version": 5,
        "description": "Instituto Nacional de Estadística e Informática de Perú.",
        "about_url": "https://iinei.inei.gob.pe/microdatos",
    },
    {
        "name": "Stat Niger (ANADO)",
        "hostname": "www.stat-niger.org",
        "country": "Niger",
        "country_code": "NE",
        "continent": "Africa",
        "version": 4,
        "description": "Institut National de la Statistique du Niger.",
        "about_url": "https://www.stat-niger.org/anado",
    },
    {
        "name": "Central Bank of Nigeria (CBN)",
        "hostname": "nada.cbn.gov.ng",
        "country": "Nigeria",
        "country_code": "NG",
        "continent": "Africa",
        "version": 5,
        "description": "Central Bank of Nigeria Statistical Data Archive.",
        "about_url": "https://nada.cbn.gov.ng",
    },
    {
        "name": "Kenya National Bureau of Statistics (KNBS)",
        "hostname": "statistics.knbs.or.ke",
        "country": "Kenya",
        "country_code": "KE",
        "continent": "Africa",
        "version": 5,
        "description": "KNBS Microdata Library.",
        "about_url": "https://statistics.knbs.or.ke",
    },
    {
        "name": "Statistics Sierra Leone",
        "hostname": "microdata.statistics.sl",
        "country": "Sierra Leone",
        "country_code": "SL",
        "continent": "Africa",
        "version": 5,
        "description": "Sierra Leone National Data Archive.",
        "about_url": "https://microdata.statistics.sl",
    },
    {
        "name": "Ethiopian Statistics Service",
        "hostname": "microdata.ess.gov.et",
        "country": "Ethiopia",
        "country_code": "ET",
        "continent": "Africa",
        "version": 5,
        "description": "Ethiopian Statistics Service Data Archive.",
        "about_url": "https://microdata.ess.gov.et",
    },
]


def load_bundled_servers_yaml() -> list[dict[str, Any]]:
    """Load servers registry from bundled or local servers.yaml file."""
    # 1. Environment variable override
    env_path = os.environ.get("NADA_SERVERS_FILE")
    if env_path and Path(env_path).is_file():
        try:
            with open(env_path, encoding="utf-8") as f:
                content = yaml.safe_load(f)
                if isinstance(content, list):
                    return content
                if isinstance(content, dict):
                    candidates = content.get("servers") or content.get("installations")
                    if isinstance(candidates, list):
                        return candidates
        except Exception as e:
            logging.debug(f"Failed loading servers from NADA_SERVERS_FILE ({env_path}): {e}")

    # 2. Package resources (wheel / installed package)
    try:
        pkg_files = importlib.resources.files("dartfx.nada")
        for rel_loc in [("data", "servers.yaml"), ("servers.yaml",)]:
            pkg_yaml = pkg_files.joinpath(*rel_loc)
            if pkg_yaml.is_file():
                content = yaml.safe_load(pkg_yaml.read_text(encoding="utf-8"))
                if isinstance(content, list):
                    return content
                if isinstance(content, dict):
                    candidates = content.get("servers") or content.get("installations")
                    if isinstance(candidates, list):
                        return candidates
    except Exception as e:
        logging.debug(f"Failed loading packaged servers.yaml: {e}")

    # 3. Source repository root (development checkout)
    for rel_path in [("data", "servers.yaml"), ("servers.yaml",)]:
        try:
            repo_file = Path(__file__).resolve().parents[3].joinpath(*rel_path)
            if repo_file.is_file():
                with open(repo_file, encoding="utf-8") as f:
                    content = yaml.safe_load(f)
                    if isinstance(content, list):
                        return content
                    if isinstance(content, dict):
                        candidates = content.get("servers") or content.get("installations")
                        if isinstance(candidates, list):
                            return candidates
        except Exception as e:
            logging.debug(f"Failed loading repo servers.yaml ({rel_path}): {e}")

    # 4. Current working directory
    for rel_path in [("data", "servers.yaml"), ("servers.yaml",)]:
        try:
            cwd_file = Path.cwd().joinpath(*rel_path)
            if cwd_file.is_file():
                with open(cwd_file, encoding="utf-8") as f:
                    content = yaml.safe_load(f)
                    if isinstance(content, list):
                        return content
                    if isinstance(content, dict):
                        candidates = content.get("servers") or content.get("installations")
                        if isinstance(candidates, list):
                            return candidates
        except Exception as e:
            logging.debug(f"Failed loading cwd servers.yaml ({rel_path}): {e}")

    return []


def fetch_nada_installations(
    target_server: str | None = None,
    country: str | None = None,
    timeout: float = 10.0,
    urls: list[str] | None = None,
    fallback_unlisted: bool = False,
) -> list[ServerInstallation]:
    """Returns a list of NADA installations from remote registry endpoints, local servers.yaml, or built-in defaults.

    Supports optional filtering by server hostname or country (ISO-2 code or country name).
    If fallback_unlisted is True and a specific target_server is requested but not found in the
    registry, a fallback ServerInstallation is synthesized to support private/unlisted servers.
    """
    if urls is not None:
        directory_urls = urls
    elif os.environ.get("NADA_SERVERS_FILE"):
        directory_urls = [os.environ["NADA_SERVERS_FILE"]]
    else:
        # Check if running in a source repository checkout (prefer local development data/servers.yaml)
        repo_yaml = Path(__file__).resolve().parents[3] / "data" / "servers.yaml"
        if repo_yaml.is_file():
            directory_urls = [str(repo_yaml)]
        else:
            directory_urls = NADA_DIRECTORY_URLS
    raw_list: list[dict[str, Any]] = []

    for entry in directory_urls:
        if not entry:
            continue

        text: str = ""
        # Check if entry is a local file
        if Path(entry).is_file():
            try:
                text = Path(entry).read_text(encoding="utf-8")
            except Exception as e:
                logging.debug(f"Failed reading local servers file {entry}: {e}")
                continue
        elif entry.startswith(("http://", "https://")):
            try:
                resp = requests.get(entry, timeout=timeout)
                if resp.status_code == 200:
                    text = resp.text
                else:
                    logging.debug(f"Registry endpoint {entry} returned HTTP {resp.status_code}")
                    continue
            except Exception as e:
                logging.debug(f"Failed fetching registry from {entry}: {e}")
                continue
        else:
            continue

        # Try parsing YAML / JSON
        try:
            parsed = yaml.safe_load(text)
            if isinstance(parsed, list):
                raw_list = parsed
                break
            elif isinstance(parsed, dict):
                candidates = parsed.get("servers") or parsed.get("installations")
                if isinstance(candidates, list):
                    raw_list = candidates
                    break
        except Exception:
            pass

        # Parse CSV format (legacy compatibility)
        try:
            csv_io = io.StringIO(text)
            reader = csv.DictReader(csv_io)
            csv_items = []
            for row in reader:
                h_map = {k.strip().lower(): v.strip() for k, v in row.items() if k and v}
                host = (
                    h_map.get("hostname")
                    or h_map.get("home")
                    or h_map.get("url")
                    or h_map.get("server")
                    or h_map.get("server url")
                    or ""
                )
                if not host:
                    continue
                name = (
                    h_map.get("name")
                    or h_map.get("title")
                    or h_map.get("repository")
                    or h_map.get("organization")
                    or host
                )
                c = h_map.get("country") or h_map.get("geocoverage") or h_map.get("nation") or ""
                cc = h_map.get("country_code") or h_map.get("code") or h_map.get("iso2") or ""
                ver = h_map.get("version") or h_map.get("server_version")
                if isinstance(ver, str):
                    clean_ver = ver.lower().replace(".x", "").replace("v", "").strip()
                    if clean_ver.isdigit():
                        ver = int(clean_ver)
                studies_cnt = h_map.get("n_studies") or h_map.get("studies_count")
                if studies_cnt:
                    clean_cnt = studies_cnt.replace(",", "").strip()
                    studies_cnt_val = int(clean_cnt) if clean_cnt.isdigit() else None
                else:
                    studies_cnt_val = None

                csv_items.append(
                    {
                        "name": name,
                        "hostname": host,
                        "country": c,
                        "country_code": cc,
                        "version": ver,
                        "status": h_map.get("status"),
                        "studies_count": studies_cnt_val,
                        "about_url": h_map.get("about_url")
                        or h_map.get("custodian")
                        or (f"https://{host}" if host else ""),
                    }
                )
            if csv_items:
                raw_list = csv_items
                break
        except Exception:
            pass

    # Fallback to local bundled servers.yaml if remote registry did not resolve
    if not raw_list:
        raw_list = load_bundled_servers_yaml()

    if not raw_list:
        raw_list = list(DEFAULT_NADA_INSTALLATIONS)

    clean_target = (
        target_server.replace("https://", "").replace("http://", "").strip("/")
        if target_server and target_server.upper() != "ALL"
        else None
    )
    if clean_target and "/" in clean_target:
        clean_target = clean_target.split("/", 1)[0]

    servers: list[ServerInstallation] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        inst = ServerInstallation.model_validate(item)
        if not inst.clean_hostname:
            continue

        # Target server filter
        if clean_target and inst.clean_hostname.lower() != clean_target.lower():
            continue

        # Country filter
        if country:
            if not matches_country(country, inst.country or "", inst.country_code or ""):
                continue

        servers.append(inst)

    # Fallback if specific target server was not found in registry
    if clean_target and not servers and fallback_unlisted:
        servers.append(
            ServerInstallation(
                hostname=clean_target,
                name=clean_target,
                country="Target Server",
                country_code="-",
            )
        )

    return servers


class NadaApiError(Exception):
    """Custom exception for NADA API errors."""

    def __init__(
        self,
        message: str,
        url: str,
        status_code: int | None = None,
        response: requests.Response | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.url = url
        self.status_code = status_code
        self.response = response

    def __str__(self) -> str:
        base_message = f"{self.message}"
        base_message += f"; URL: {self.url}"
        if self.status_code is not None:
            base_message += f"; Status Code: {self.status_code}"
        return base_message


class SearchParameters(BaseModel):
    """Represents parameters passed to the NADA catalog search endpoint.
    See NADA OpenAPI specification (/catalog/search).
    """

    model_config = ConfigDict(populate_by_name=True)

    sk: str = Field(default="*", description="Keywords for searching study level metadata.")
    country: str | None = Field(default=None, description="Country name or ISO 3 code (pipe-separated).")
    from_year: int | None = Field(default=None, alias="from", description="Starting year for data collection.")
    to_year: int | None = Field(default=None, alias="to", description="End year for data collection.")
    collection: str | None = Field(default=None, description="Filter results by collection(s).")
    created: str | None = Field(default=None, description="Filter results by date of creation (YYYY-MM-DD).")
    dtype: Literal["open", "direct", "public", "licensed", "enclave", "remote", "other"] | str | None = Field(
        default=None, description="Filter by data access types."
    )
    ps: int | None = Field(default=15, ge=1, le=1000, description="Results per page (default: 15).")
    page: int | None = Field(default=1, ge=1, description="Page to return for the search result.")
    sort_by: Literal["rank", "title", "nation", "year"] | str | None = Field(
        default=None, description="Sort field (rank, title, nation, year)."
    )
    sort_order: Literal["asc", "desc"] | str | None = Field(default=None, description="Sort order (asc, desc).")
    inc_iso: bool | None = Field(default=None, description="Include ISO3 country codes for the results.")
    format: str | None = Field(default="json", description="Output format (json, xml).")


def _get_caller_name() -> str:
    """Returns the name of the function that called the current function."""
    frame = inspect.currentframe()
    if frame is None:
        return "<unknown>"
    try:
        caller_frame = frame.f_back.f_back if frame.f_back else None
        return caller_frame.f_code.co_name if caller_frame else "<unknown>"
    finally:
        del frame


class NadaServer(BaseModel):
    """Primary interface for communicating with a NADA repository."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    installation: ServerInstallation
    api_key: str | None = None
    directory: str | None = None
    api_base_path: str = "index.php/api"
    on_api_error: Literal["raise", "none"] = "raise"
    on_api_success_return: Literal["json", "text", "response"] = "json"
    session: requests_cache.CachedSession = Field(
        default_factory=lambda: requests_cache.CachedSession(backend="memory", cache_name="nada")
    )
    user_agent: str = Field(
        default=f"Mozilla/5.0 (compatible; dartfx-nada/{__version__}; +https://github.com/DataArtifex/nada-toolkit)"
    )
    ssl_verify: bool | str = True
    version: int | None = None

    def __init__(
        self,
        server: str | ServerInstallation | None = None,
        api_key: str | None = None,
        on_api_error: Literal["raise", "none"] = "raise",
        on_api_success_return: Literal["json", "text", "response"] = "json",
        session: requests_cache.CachedSession | None = None,
        lookup_installation: bool = True,
        version: int | None = None,
        directory: str | None = None,
        **kwargs: Any,
    ) -> None:
        if server is None:
            server = os.environ.get("NADA_SERVER") or os.environ.get("NADA_HOSTNAME") or "microdata.worldbank.org"

        if api_key is None:
            api_key = (
                os.environ.get("DARTFX_NADA_API_KEY")
                or os.environ.get("NADA_API_KEY")
                or os.environ.get("NADA_API_TOKEN")
            )

        if isinstance(server, str):
            clean_host = server.replace("https://", "").replace("http://", "").strip("/")
            if "/" in clean_host:
                clean_host = clean_host.split("/", 1)[0]
            server_inst = ServerInstallation(hostname=clean_host)
            if lookup_installation:
                for inst in fetch_nada_installations(target_server=clean_host, fallback_unlisted=False):
                    if inst.clean_hostname.lower() == clean_host.lower():
                        server_inst = inst
                        break
        else:
            server_inst = server

        if not isinstance(server_inst, ServerInstallation):
            raise TypeError("server must be either a hostname or a ServerInstallation")

        if session is None:
            session = requests_cache.CachedSession(backend="memory", cache_name="nada")

        if "user_agent" not in kwargs:
            env_ua = os.environ.get("NADA_USER_AGENT") or os.environ.get("DARTFX_NADA_USER_AGENT")
            if env_ua:
                kwargs["user_agent"] = env_ua.strip()

        if "ssl_verify" not in kwargs and server_inst.url.startswith("https://"):
            from dartfx.nada.harvester import get_ca_bundle_for_host

            cached_bundle = get_ca_bundle_for_host(server_inst.clean_hostname)
            if cached_bundle:
                kwargs["ssl_verify"] = cached_bundle

        super().__init__(
            installation=server_inst,
            api_key=api_key,
            directory=directory,
            on_api_error=on_api_error,
            on_api_success_return=on_api_success_return,
            session=session,
            version=version,
            **kwargs,
        )

    @property
    def url(self) -> str:
        """Return HTTPS URL for the server."""
        return self.installation.url

    def detect_server_version(self) -> int:
        """Autodetect NADA server version (v5 vs v4)."""
        if self.version is not None:
            return self.version

        # 1. Probe v5 search endpoint
        v5_url = f"{self.url}/index.php/api/catalog/search?ps=1"
        try:
            resp = self.session.get(v5_url, verify=False, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                if "result" in data and "rows" in data["result"]:
                    self.version = 5
                    return 5
        except Exception:
            pass

        # 2. Probe v4 CSV export endpoint
        v4_url = f"{self.url}/index.php/catalog/export/csv?ps=1"
        try:
            resp = self.session.get(v4_url, verify=False, timeout=8)
            if resp.status_code == 200:
                data = resp.text
                if data.startswith("\ufeff"):
                    data = data[1:]
                if data.startswith("id,surveyid"):
                    self.version = 4
                    return 4
        except Exception:
            pass

        # Default fallback to 5
        self.version = 5
        return 5

    def request(
        self,
        method: str,
        path: str,
        description: str | None = None,
        headers: dict[str, str] | None = None,
        success: int = 200,
        return_type: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Execute an API request against the NADA server."""
        default_headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": self.user_agent,
        }
        if self.api_key:
            default_headers["X-API-KEY"] = self.api_key

        request_headers = default_headers | (headers or {})

        clean_path = path.lstrip("/")
        if clean_path.startswith("http"):
            url = clean_path
        elif clean_path.startswith("index.php/"):
            url = f"{self.url}/{clean_path}"
        elif clean_path.startswith("api/"):
            url = f"{self.url}/index.php/{clean_path}"
        else:
            url = f"{self.url}/{self.api_base_path}/{clean_path}"

        try:
            response = self.session.request(method, url, headers=request_headers, verify=self.ssl_verify, **kwargs)
        except requests.exceptions.SSLError as ssl_err:
            if not url.startswith("https://"):
                raise
            response = None
            err_str = str(ssl_err)
            if "unable to get local issuer certificate" in err_str:
                try:
                    from dartfx.nada.harvester import resolve_aia_intermediate_ca

                    bundle = resolve_aia_intermediate_ca(self.installation.clean_hostname)
                    if bundle:
                        self.ssl_verify = str(bundle)
                        response = self.session.request(
                            method, url, headers=request_headers, verify=self.ssl_verify, **kwargs
                        )
                except Exception:
                    response = None

            if response is None:
                http_url = "http://" + url[len("https://") :]
                logging.warning(f"SSL verification failed for {url}. Attempting HTTP fallback to {http_url}")
                try:
                    response = self.session.request(method, http_url, headers=request_headers, **kwargs)
                    self.installation.scheme = "http"
                except Exception:
                    raise ssl_err from None

        assert response is not None
        actual_return_type = return_type or self.on_api_success_return

        if response.status_code == success:
            if actual_return_type == "json":
                try:
                    return response.json()
                except json.JSONDecodeError as e:
                    msg = f"{description or 'NADA API'} -- JSONDecodeError: {e.msg}"
                    logging.error(msg)
                    if self.on_api_error != "none":
                        raise NadaApiError(msg, url, response.status_code, response) from e
                    return None
            elif actual_return_type == "text":
                return response.text
            return response

        logging.error(f"{description or 'NADA API'} -- HTTP {response.status_code}")
        if self.on_api_error != "none":
            raise NadaApiError(description or "NADA API Error", url, response.status_code, response)
        return None

    def get_request(
        self,
        path: str,
        description: str | None = None,
        headers: dict[str, str] | None = None,
        success: int = 200,
        return_type: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Call the API using GET."""
        if not description:
            description = _get_caller_name()
        return self.request(
            "get", path, description, headers=headers, success=success, return_type=return_type, **kwargs
        )

    def post_request(
        self,
        path: str,
        description: str | None = None,
        headers: dict[str, str] | None = None,
        success: int = 200,
        **kwargs: Any,
    ) -> Any:
        """Call the API using POST."""
        if not description:
            description = _get_caller_name()
        return self.request("post", path, description, headers=headers, success=success, **kwargs)

    #
    # COLLECTIONS
    #
    def get_collections(self) -> Any:
        """List all collections available in the catalog."""
        return self.get_request("catalog/collections")

    def get_collection(self, repo_id: str) -> Any:
        """Get details for a single collection by repoId."""
        return self.get_request(f"catalog/collections/{repo_id}")

    #
    # CATALOG & CODES
    #
    def get_country_codes(self) -> Any:
        """List all country codes supported by the catalog."""
        return self.get_request("catalog/country_codes")

    def get_data_access_codes(self) -> Any:
        """List data access type codes."""
        return self.get_request("catalog/data_access_codes")

    def get_latest_studies(self) -> Any:
        """List latest studies added to the catalog."""
        return self.get_request("catalog/latest")

    #
    # SEARCH
    #
    def search_simple(self, q: str = "*", **kwargs: Any) -> Any:
        """Search catalog studies using a simple keyword query."""
        params = SearchParameters(sk=q, **kwargs)
        return self.search(params)

    def search(self, parameters: SearchParameters) -> Any:
        """Search catalog studies using structured SearchParameters."""
        params_dict = parameters.model_dump(by_alias=True, exclude_none=True)
        return self.get_request("catalog/search", description="Catalog Search", params=params_dict)

    #
    # STUDY METADATA & EXPORTS
    #
    def get_study(self, idno: str, id_format: str | None = None) -> Any:
        """Get study level metadata in JSON."""
        params = {"id_format": id_format} if id_format else None
        return self.get_request(f"catalog/{idno}", params=params)

    def get_study_ddi(self, idno: str, id_format: str | None = None, numeric_id: int | str | None = None) -> Any:
        """Download study DDI CodeBook XML."""
        if numeric_id and ("/" in idno or "%2F" in idno):
            return self.get_request(f"index.php/metadata/export/{numeric_id}/ddi", return_type="text")
        params = {"id_format": id_format} if id_format else None
        try:
            return self.get_request(f"catalog/ddi/{idno}", params=params, return_type="text")
        except Exception:
            if numeric_id:
                return self.get_request(f"index.php/metadata/export/{numeric_id}/ddi", return_type="text")
            raise

    def get_study_json(self, idno: str, id_format: str | None = None, numeric_id: int | str | None = None) -> Any:
        """Download study metadata as raw JSON."""
        if numeric_id and ("/" in idno or "%2F" in idno):
            return self.get_request(f"index.php/metadata/export/{numeric_id}/json")
        params = {"id_format": id_format} if id_format else None
        try:
            return self.get_request(f"catalog/json/{idno}", params=params)
        except Exception:
            if numeric_id:
                return self.get_request(f"index.php/metadata/export/{numeric_id}/json")
            raise

    def get_study_resources(self, idno: str, id_format: str | None = None) -> Any:
        """Download study external resources and questionnaires as JSON."""
        params = {"id_format": id_format} if id_format else None
        return self.get_request(f"catalog/resources/{idno}", params=params)

    def get_study_croissant(self, idno: str, id_format: str | None = None) -> Any:
        """Download study metadata formatted as MLCommons Croissant JSON-LD."""
        params = {"id_format": id_format} if id_format else None
        return self.get_request(f"catalog/croissant/{idno}", params=params)

    def get_study_schema(self, idno: str, numeric_id: int | str | None = None) -> Any:
        """Download and extract Schema.org Dataset JSON-LD metadata from the study catalog landing page."""
        clean_idno = idno.strip()
        candidate_paths = [
            f"index.php/catalog/study/{clean_idno}",
        ]
        if numeric_id and str(numeric_id).strip() != clean_idno:
            candidate_paths.append(f"index.php/catalog/{urllib.parse.quote(str(numeric_id).strip())}")
        candidate_paths.append(f"index.php/catalog/{clean_idno}")

        for p in candidate_paths:
            try:
                html = self.get_request(p, return_type="text")
                if html and "application/ld+json" in html:
                    m = re.search(
                        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                        html,
                        re.DOTALL | re.IGNORECASE,
                    )
                    if m:
                        return json.loads(m.group(1).strip())
            except Exception:
                continue

        logging.error(f"Schema.org JSON-LD not found for study {idno}")
        if self.on_api_error != "none":
            raise NadaApiError(
                f"Schema.org JSON-LD not found on study landing page for {idno}",
                f"{self.url}/index.php/catalog/study/{clean_idno}",
                404,
                None,
            )
        return None

    def get_data_files(self, idno: str, id_format: str | None = None) -> Any:
        """List all data files for a study."""
        params = {"id_format": id_format} if id_format else None
        return self.get_request(f"catalog/{idno}/data_files", params=params)

    def get_data_file(self, idno: str, file_id: str, id_format: str | None = None) -> Any:
        """Get metadata for a single data file."""
        params = {"id_format": id_format} if id_format else None
        return self.get_request(f"catalog/{idno}/data_files/{file_id}", params=params)

    def get_variables(self, idno: str, id_format: str | None = None) -> Any:
        """List variables by study."""
        params = {"id_format": id_format} if id_format else None
        return self.get_request(f"catalog/{idno}/variables", params=params)

    def get_data_file_variables(self, idno: str, file_id: str, id_format: str | None = None) -> Any:
        """Get variables attached to a data file."""
        params = {"id_format": id_format} if id_format else None
        return self.get_request(f"catalog/{idno}/data_files/{file_id}/variables", params=params)

    def get_variable(self, idno: str, var_id: str, id_format: str | None = None) -> Any:
        """Get a single variable's metadata."""
        params = {"id_format": id_format} if id_format else None
        return self.get_request(f"catalog/{idno}/variables/{var_id}", params=params)
