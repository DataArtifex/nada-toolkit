# SPDX-FileCopyrightText: 2024-present kulnor <pascal.heus@gmail.com>
#
# SPDX-License-Identifier: MIT
from dotenv import find_dotenv, load_dotenv

from .__about__ import __version__
from .harvester import (
    ServerHarvester,
    abbreviate_error,
    analyze_harvest_errors,
    classify_harvest_error,
    display_server_stats,
    extract_server_environment,
    fetch_active_studies,
    fetch_active_surveys,
    fetch_server_stats,
    format_dataset_url,
    format_php_version,
    format_response_latency,
    format_version,
    get_server_dir_name,
    resolve_server_token,
    save_server_token,
)
from .markdown import (
    check_servers_markdown,
    generate_servers_markdown,
    render_servers_markdown,
)
from .nada import (
    COUNTRY_TO_ISO2,
    DEFAULT_NADA_INSTALLATIONS,
    ISO2_TO_COUNTRY,
    NADA_DIRECTORY_URLS,
    Custodian,
    GeoCoverage,
    NadaApiError,
    NadaServer,
    SearchParameters,
    ServerInstallation,
    fetch_nada_installations,
    get_iso2_code,
    load_bundled_servers_yaml,
    matches_country,
)

# Auto-load environment variables from .env file
load_dotenv(find_dotenv(usecwd=True))

__all__ = [
    "__version__",
    "COUNTRY_TO_ISO2",
    "Custodian",
    "DEFAULT_NADA_INSTALLATIONS",
    "GeoCoverage",
    "ISO2_TO_COUNTRY",
    "NADA_DIRECTORY_URLS",
    "NadaApiError",
    "NadaServer",
    "SearchParameters",
    "ServerInstallation",
    "ServerHarvester",
    "abbreviate_error",
    "analyze_harvest_errors",
    "check_servers_markdown",
    "classify_harvest_error",
    "display_server_stats",
    "extract_server_environment",
    "fetch_active_studies",
    "fetch_active_surveys",
    "fetch_nada_installations",
    "fetch_server_stats",
    "format_dataset_url",
    "format_php_version",
    "format_response_latency",
    "format_version",
    "generate_servers_markdown",
    "get_iso2_code",
    "get_server_dir_name",
    "load_bundled_servers_yaml",
    "matches_country",
    "render_servers_markdown",
    "resolve_server_token",
    "save_server_token",
]
