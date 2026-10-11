dartfx-nada Documentation
=========================

**A modern Python toolkit and metadata harvester for IHSN NADA repositories**

Overview
--------

``dartfx-nada`` is a type-safe Python toolkit and CLI utility for discovering, querying, and bulk-harvesting metadata from `IHSN NADA <https://nada.ihsn.org/>`_ server instances.

NADA (National Data Archive) is an open-source data cataloging system widely used by national statistical offices (NSOs), central banks, international organizations (e.g., World Bank Microdata Library, ILO, UNHCR), and research institutions worldwide to publish microdata, survey documentation, and DDI metadata.

Key Capabilities
~~~~~~~~~~~~~~~~

* 🌍 **Worldwide Server Discovery**: Registry engine supporting known NADA deployments worldwide, with ISO 3166-1 alpha-2 country crosswalk and filtering.
* 🔍 **Catalog Search**: Comprehensive search client with support for keyword queries, collection filters, date ranges, and sorting.
* 📦 **Multi-Format Export & Retrieval**: Retrieve study metadata in DDI CodeBook 2.5 XML (``ddi``), NADA study metadata JSON (``json``), documentation / external resources (``resources``), MLCommons Croissant (``croissant``), and Google Dataset Search Schema.org (``schema``).
* 🚜 **Resilient Incremental Harvester**: Multi-threaded concurrent synchronization (``--workers`` / ``-w``) backed by ``.manifest.json``, fast timestamp matching, SHA-256 integrity verification, 24-hour catalog caching, and comprehensive error persistence with automatic recoverable failure retries.
* 📊 **Server Diagnostics & Performance Latency**: Parallel query latency benchmarking with semantic color badges and deep SSL/TLS diagnostics.
* 🛡️ **Modern Architecture**: Python 3.12+, Pydantic v2 data models, Typer CLI, and Rich terminal visualizers.

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   diagnostics
   changelog

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
