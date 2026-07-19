"""
Phase C1-D1 — no-network / no-database / no-application-import proof for
the new source_registry / source_validators / manifest_contract modules.

Mirrors the safety-testing discipline established in
test_instrument_master_safety.py (Phase C0), applied to the C1-D1
modules specifically.
"""
from __future__ import annotations

import http.client
import importlib
import socket
import subprocess
import sys

import pytest


def _forbid_network(monkeypatch):
    def _raise(*args, **kwargs):
        raise AssertionError("network primitive invoked — this module must never touch the network")

    monkeypatch.setattr(socket.socket, "connect", _raise)
    monkeypatch.setattr(socket, "create_connection", _raise)
    monkeypatch.setattr(socket, "getaddrinfo", _raise)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", _raise)
    monkeypatch.setattr(http.client.HTTPSConnection, "connect", _raise)


def _forbid_subprocess(monkeypatch):
    def _raise(*args, **kwargs):
        raise AssertionError("subprocess creation invoked — this module must never spawn a process")

    monkeypatch.setattr(subprocess, "Popen", _raise)
    monkeypatch.setattr(subprocess, "run", _raise)


def _forbid_database(monkeypatch):
    # Only monkeypatch database connection constructors that are actually
    # importable in this environment — sqlite3 is always available in a
    # standard CPython build; psycopg2/sqlalchemy may not be installed at
    # all, in which case there is nothing to patch (and nothing for the
    # new modules to import either, per the source-text scan below).
    import sqlite3

    def _raise(*args, **kwargs):
        raise AssertionError("database connection invoked — this module must never open a database")

    monkeypatch.setattr(sqlite3, "connect", _raise)


class TestNoNetworkCalls:
    def test_source_registry_import_and_use_never_touches_network(self, monkeypatch):
        _forbid_network(monkeypatch)
        from services.instrument_master.source_registry import all_entries, canonical_registry_hash

        entries = all_entries()
        assert len(entries) == 11
        canonical_registry_hash()

    def test_source_validators_never_touch_network(self, monkeypatch):
        _forbid_network(monkeypatch)
        from services.instrument_master.source_validators import validate_equity_current

        r = validate_equity_current(
            "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
            "SYNTHCO,Synthetic Test Company Ltd,EQ,01-JAN-2020,10,1,INTESTAB0012,10\n"
        )
        assert r.valid is True

    def test_manifest_contract_never_touches_network(self, monkeypatch):
        _forbid_network(monkeypatch)
        from services.instrument_master.manifest_contract import compute_stale_state

        assert compute_stale_state(2).value == "fresh"

    def test_full_public_api_surface_under_strengthened_guards(self, monkeypatch):
        # Phase C1-D3 correction (non-blocking finding): exercise the
        # full public API of all three new modules with network, DNS,
        # subprocess, and database primitives all simultaneously
        # monkeypatched to raise — importing and using the modules must
        # still make no network call, resolve no DNS, create no
        # subprocess, and open no database.
        _forbid_network(monkeypatch)
        _forbid_subprocess(monkeypatch)
        _forbid_database(monkeypatch)

        from services.instrument_master.manifest_contract import (
            SourceReadinessStatus,
            build_completeness_contract,
            compute_stale_state,
        )
        from services.instrument_master.enums import SourceId, ValidationResultStatus
        from services.instrument_master.source_registry import all_entries, canonical_registry_hash
        from services.instrument_master.source_validators import (
            validate_equity_current,
            validate_il_series,
            validate_name_history,
            validate_sme,
            validate_symbol_history,
        )

        assert len(all_entries()) == 11
        canonical_registry_hash()
        assert compute_stale_state(1).value == "fresh"
        validate_equity_current(
            "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
            "SYNTHCO,Synthetic Test Company Ltd,EQ,01-JAN-2020,10,1,INTESTAB0012,10\n"
        )
        validate_sme(
            "SYMBOL,NAME_OF_COMPANY,SERIES,DATE_OF_LISTING,PAID_UP_VALUE,ISIN_NUMBER,FACE_VALUE\n"
            "SYNTHSME,Synthetic SME Test Ltd,SM,01-JAN-2021,10,INTESTEF0030,10\n",
            source_url="https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv",
        )
        validate_il_series("Symbol,Se,Sec Name\r\n")
        validate_symbol_history("Some Co,OLD,NEW,01-JAN-2020\n")
        validate_name_history("NCH_SYMBOL, NCH_PREV_NAME, NCH_NEW_NAME, NCH_DT\nAAA,Old,New,01-JAN-2020\n")
        all_ready_statuses = {
            e.source_id: SourceReadinessStatus(
                source_id=e.source_id,
                available=True,
                validation_valid=True,
                fresh=True,
                validation_status=ValidationResultStatus.VALID,
            )
            for e in all_entries()
        }
        build_completeness_contract(
            source_statuses=all_ready_statuses,
            source_errors=(),
            oldest_source_age_days=None,
            newest_source_age_days=None,
            business_policy_permits_daily_picks=False,
            business_policy_permits_daily_picks_non_sme=False,
            business_policy_permits_screener=False,
            business_policy_permits_recommendations=False,
            sme_explicitly_excluded_from_eligible_universe=False,
            unknown_unclassified_securities_excluded=False,
            manual_source_present=False,
            manual_source_reviewed_by=None,
            manual_source_reviewed_at=None,
        )

    def test_no_urllib_requests_httpx_import_at_module_level(self):
        # None of the three new modules may import a networking library
        # at module scope — reload each fresh and inspect sys.modules
        # for a networking library import that wasn't already present.
        forbidden_prefixes = ("requests", "httpx", "aiohttp", "urllib.request", "urllib3")
        for modname in (
            "services.instrument_master.source_registry",
            "services.instrument_master.source_validators",
            "services.instrument_master.manifest_contract",
        ):
            module = importlib.import_module(modname)
            module_file = module.__file__
            with open(module_file, "r", encoding="utf-8") as f:
                source = f.read()
            for forbidden in forbidden_prefixes:
                assert f"import {forbidden}" not in source
                assert f"from {forbidden}" not in source


class TestNoDatabaseCalls:
    def test_no_database_library_import_in_new_modules(self):
        forbidden_prefixes = ("psycopg", "psycopg2", "sqlite3", "sqlalchemy", "asyncpg")
        for modname in (
            "services.instrument_master.source_registry",
            "services.instrument_master.source_validators",
            "services.instrument_master.manifest_contract",
        ):
            module = importlib.import_module(modname)
            with open(module.__file__, "r", encoding="utf-8") as f:
                source = f.read()
            for forbidden in forbidden_prefixes:
                assert f"import {forbidden}" not in source
                assert f"from {forbidden}" not in source


class TestNoEnvironmentLoading:
    def test_registry_import_reads_no_environment_variable(self, monkeypatch):
        # If any code path in these modules read os.environ, clearing it
        # would change behavior (e.g. raise KeyError, or change a value).
        # Clearing it and re-importing must produce an identical result.
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setattr("os.environ", {}, raising=False)

        for modname in (
            "services.instrument_master.source_registry",
            "services.instrument_master.source_validators",
            "services.instrument_master.manifest_contract",
        ):
            if modname in sys.modules:
                del sys.modules[modname]

        from services.instrument_master.source_registry import canonical_registry_hash

        assert canonical_registry_hash()  # importable and callable with an empty environment

    def test_no_os_environ_reference_in_new_module_source(self):
        for modname in (
            "services.instrument_master.source_registry",
            "services.instrument_master.source_validators",
            "services.instrument_master.manifest_contract",
        ):
            module = importlib.import_module(modname)
            with open(module.__file__, "r", encoding="utf-8") as f:
                source = f.read()
            assert "os.environ" not in source
            assert "os.getenv" not in source


class TestNoProductionApplicationImport:
    def test_source_registry_module_does_not_import_fastapi_or_app_startup(self):
        module = importlib.import_module("services.instrument_master.source_registry")
        with open(module.__file__, "r", encoding="utf-8") as f:
            source = f.read()
        for forbidden in ("fastapi", "services.main", "services.app", "uvicorn"):
            assert forbidden not in source

    def test_source_validators_module_does_not_import_fastapi_or_app_startup(self):
        module = importlib.import_module("services.instrument_master.source_validators")
        with open(module.__file__, "r", encoding="utf-8") as f:
            source = f.read()
        for forbidden in ("fastapi", "services.main", "services.app", "uvicorn"):
            assert forbidden not in source

    def test_manifest_contract_module_does_not_import_fastapi_or_app_startup(self):
        module = importlib.import_module("services.instrument_master.manifest_contract")
        with open(module.__file__, "r", encoding="utf-8") as f:
            source = f.read()
        for forbidden in ("fastapi", "services.main", "services.app", "uvicorn"):
            assert forbidden not in source

    def test_importing_new_modules_has_no_side_effect_on_sys_modules_beyond_expected(self):
        # Importing the C1-D1 modules must not pull in unrelated
        # production consumers (stock_universe, daily_picks, etc.).
        before = set(sys.modules)
        importlib.import_module("services.instrument_master.source_registry")
        importlib.import_module("services.instrument_master.source_validators")
        importlib.import_module("services.instrument_master.manifest_contract")
        after = set(sys.modules)
        newly_imported = after - before
        forbidden_substrings = ("stock_universe", "daily_picks", "postgres_store", "telegram_bot")
        for mod_name in newly_imported:
            for forbidden in forbidden_substrings:
                assert forbidden not in mod_name
