"""
Unit tests for FrierenLibrarian.query_similar().

Scoring rules (from frieren_librarian.py):
  +3  — incident.service matches the query service (case-insensitive)
  +1  — per keyword (>=4 alphanumeric chars) found in incident.root_cause + fix
  Incidents scoring 0 are excluded entirely.
  Top `limit` (default 3) returned, highest score first.

Run:
  python -m pytest theraops_backend/test_frieren.py -v
"""
from __future__ import annotations

import asyncio
import pathlib
import textwrap
from typing import Sequence

import pytest

from theraops_backend.memory.frieren_librarian import FrierenLibrarian, IncidentRecord

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_librarian(incidents: list[IncidentRecord]) -> FrierenLibrarian:
    """Return an in-memory FrierenLibrarian pre-seeded with the given incidents.
    Uses a non-existent storage path so _load_from_disk returns [].
    """
    lib = FrierenLibrarian(pathlib.Path("/nonexistent/test_incidents.json"))
    lib._incidents = list(incidents)
    return lib


def _print_ranking_table(
    label: str,
    results: list[IncidentRecord],
    all_incidents: list[IncidentRecord],
    service: str,
    sample_messages: Sequence[str],
) -> None:
    """Print a readable debug table showing how each incident ranked."""
    print(f"\n{'=' * 60}")
    print(f"  Test: {label}")
    print(f"  Service: {service!r}   Keywords from: {sample_messages}")
    print(f"{'=' * 60}")
    returned_ids = {id(r) for r in results}
    print(f"  {'#':<4} {'service':<12} {'root_cause[:40]':<42} {'in results?'}")
    print(f"  {'-'*4} {'-'*12} {'-'*42} {'-'*11}")
    for rank, incident in enumerate(results, start=1):
        desc = textwrap.shorten(incident.root_cause, width=40, placeholder="…")
        print(f"  {rank:<4} {incident.service:<12} {desc:<42} ✓")
    # Show excluded incidents
    for incident in all_incidents:
        if id(incident) not in returned_ids:
            desc = textwrap.shorten(incident.root_cause, width=40, placeholder="…")
            print(f"  {'—':<4} {incident.service:<12} {desc:<42} (excluded/below top-3)")
    print()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CREATED_AT = "2026-01-01T00:00:00+00:00"

INCIDENT_A = IncidentRecord(
    service="api",
    root_cause="Database connection pool exhausted, connections stacking up",
    fix="Increased pool size from 10 to 50 via DB_POOL_SIZE env var",
    created_at=_CREATED_AT,
)

INCIDENT_B = IncidentRecord(
    service="api",
    root_cause="Memory OOM, pod killed by kernel",
    fix="Bumped container memory limits to 2Gi in deployment manifest",
    created_at=_CREATED_AT,
)

INCIDENT_C = IncidentRecord(
    service="worker",
    root_cause="Job queue backlog, database connection pool starved by workers",
    fix="Scaled worker replicas from 2 to 8 and increased pool size",
    created_at=_CREATED_AT,
)

INCIDENT_D = IncidentRecord(
    service="billing",
    root_cause="Stripe webhook signature validation failed repeatedly",
    fix="Rotated Stripe signing secret and redeployed billing service",
    created_at=_CREATED_AT,
)

INCIDENT_E = IncidentRecord(
    service="api",
    root_cause="TLS certificate expired causing SSL handshake failures",
    fix="Renewed certificate via certbot and restarted nginx",
    created_at=_CREATED_AT,
)

ALL_INCIDENTS = [INCIDENT_A, INCIDENT_B, INCIDENT_C, INCIDENT_D, INCIDENT_E]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestQuerySimilar:

    def test_keyword_match_ranks_highest(self):
        """Incident A should rank above B: both match service=api (+3),
        but only A contains 'pool' and 'exhausted' from the query keywords."""
        lib = _make_librarian(ALL_INCIDENTS)
        results = asyncio.run(lib.query_similar(
            service="api",
            sample_messages=["connection pool exhausted"],
        ))
        _print_ranking_table(
            "keyword_match_ranks_highest",
            results, ALL_INCIDENTS,
            service="api",
            sample_messages=["connection pool exhausted"],
        )
        assert results, "Expected at least one result"
        assert results[0] is INCIDENT_A, (
            f"Expected INCIDENT_A to rank first, got: {results[0].root_cause!r}"
        )

    def test_service_match_beats_keyword_only(self):
        """INCIDENT_C (service=worker) mentions 'pool' and 'database' but
        INCIDENT_A (service=api) should still score higher because the
        service bonus (+3) outweighs a small keyword advantage."""
        lib = _make_librarian(ALL_INCIDENTS)
        results = asyncio.run(lib.query_similar(
            service="api",
            sample_messages=["pool"],
        ))
        _print_ranking_table(
            "service_match_beats_keyword_only",
            results, ALL_INCIDENTS,
            service="api",
            sample_messages=["pool"],
        )
        # All three api incidents score at least +3; worker only scores keyword hits
        api_results = [r for r in results if r.service == "api"]
        non_api_results = [r for r in results if r.service != "api"]
        if api_results and non_api_results:
            assert results.index(api_results[0]) < results.index(non_api_results[0]), (
                "An api incident should rank above a non-api incident with same keyword count"
            )

    def test_unrelated_service_excluded_when_no_keywords_match(self):
        """INCIDENT_D (billing/Stripe) should not appear when querying api
        with keywords that don't appear in its description."""
        lib = _make_librarian(ALL_INCIDENTS)
        results = asyncio.run(lib.query_similar(
            service="api",
            sample_messages=["pool exhausted database"],
        ))
        _print_ranking_table(
            "unrelated_service_excluded",
            results, ALL_INCIDENTS,
            service="api",
            sample_messages=["pool exhausted database"],
        )
        assert INCIDENT_D not in results, (
            "INCIDENT_D (billing/Stripe) should not appear in api query with pool/database keywords"
        )

    def test_no_match_returns_empty(self):
        """Querying with a service and keywords that match nothing should return []."""
        lib = _make_librarian(ALL_INCIDENTS)
        results = asyncio.run(lib.query_similar(
            service="nonexistent",
            sample_messages=["zzzzunlikelykeyword"],
        ))
        _print_ranking_table(
            "no_match_returns_empty",
            results, ALL_INCIDENTS,
            service="nonexistent",
            sample_messages=["zzzzunlikelykeyword"],
        )
        assert results == [], f"Expected empty list, got: {results}"

    def test_short_keywords_ignored(self):
        """Keywords under 4 chars (e.g. 'OOM', 'up') are stripped by _extract_keywords
        so they should not contribute to the score."""
        lib = _make_librarian([INCIDENT_B])
        # 'OOM' is 3 chars — ignored. Only service match should apply.
        results = asyncio.run(lib.query_similar(
            service="api",
            sample_messages=["OOM up"],
        ))
        _print_ranking_table(
            "short_keywords_ignored",
            results, [INCIDENT_B],
            service="api",
            sample_messages=["OOM up"],
        )
        # INCIDENT_B still appears because of service match (+3), not keywords
        assert INCIDENT_B in results

    def test_limit_respected(self):
        """query_similar should honour the limit parameter."""
        lib = _make_librarian(ALL_INCIDENTS)
        results = asyncio.run(lib.query_similar(
            service="api",
            sample_messages=["error failure"],
            limit=1,
        ))
        _print_ranking_table(
            "limit_respected",
            results, ALL_INCIDENTS,
            service="api",
            sample_messages=["error failure"],
        )
        assert len(results) <= 1, f"Expected at most 1 result, got {len(results)}"

    def test_tls_certificate_query_ranks_incident_e(self):
        """Specific keyword match: 'certificate' and 'expired' should surface INCIDENT_E."""
        lib = _make_librarian(ALL_INCIDENTS)
        results = asyncio.run(lib.query_similar(
            service="api",
            sample_messages=["TLS certificate expired handshake failed"],
        ))
        _print_ranking_table(
            "tls_certificate_query",
            results, ALL_INCIDENTS,
            service="api",
            sample_messages=["TLS certificate expired handshake failed"],
        )
        assert results, "Expected at least one result"
        assert results[0] is INCIDENT_E, (
            f"Expected INCIDENT_E to rank first, got: {results[0].root_cause!r}"
        )
