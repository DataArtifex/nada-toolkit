# SPDX-FileCopyrightText: 2024-present kulnor <pascal.heus@gmail.com>
#
# SPDX-License-Identifier: MIT
from dotenv import find_dotenv, load_dotenv

from .harvester import (
    ServerHarvester,
    abbreviate_error,
    analyze_harvest_errors,
    classify_harvest_error,
    display_server_stats,
    fetch_active_studies,
    fetch_server_stats,
    format_dataset_url,
    format_response_latency,
    format_version,
    resolve_server_token,
    save_server_token,
)
from .nada import (
    COUNTRY_TO_ISO2,
    DEFAULT_NADA_INSTALLATIONS,
    NADA_DIRECTORY_URLS,
    NadaApiError,
    NadaServer,
    SearchParameters,
    ServerInstallation,
    fetch_nada_installations,
    get_iso2_code,
    matches_country,
)

# Auto-load environment variables from .env file
load_dotenv(find_dotenv(usecwd=True))

__all__ = [
    "COUNTRY_TO_ISO2",
    "DEFAULT_NADA_INSTALLATIONS",
    "NADA_DIRECTORY_URLS",
    "NadaApiError",
    "NadaServer",
    "SearchParameters",
    "ServerInstallation",
    "ServerHarvester",
    "abbreviate_error",
    "analyze_harvest_errors",
    "classify_harvest_error",
    "display_server_stats",
    "fetch_active_studies",
    "fetch_nada_installations",
    "fetch_server_stats",
    "format_dataset_url",
    "format_response_latency",
    "format_version",
    "get_iso2_code",
    "matches_country",
    "resolve_server_token",
    "save_server_token",
]
