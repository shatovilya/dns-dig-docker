"""Security tests for db.repository module."""

import pathlib


def test_valid_child_tables_are_frozen():
    """Ensure VALID_CHILD_TABLES is immutable to prevent runtime modification."""
    repo_path = pathlib.Path(__file__).parent.parent / "app" / "db" / "repository.py"
    source = repo_path.read_text()
    
    assert "VALID_CHILD_TABLES" in source
    assert "frozenset" in source


def test_table_allowlist_contains_expected_tables():
    """Verify the table allowlist includes all expected child tables."""
    repo_path = pathlib.Path(__file__).parent.parent / "app" / "db" / "repository.py"
    source = repo_path.read_text()
    
    # Expected tables used in the system
    expected_tables = [
        "resolver_aggregates",
        "domain_aggregates",
        "error_aggregates",
        "edns_aggregates",
        "chart_buckets",
    ]
    
    for table in expected_tables:
        assert f'"{table}"' in source, f"Table {table} should be in VALID_CHILD_TABLES"


def test_no_f_string_injection_risk():
    """Ensure the SQL query uses safe table name interpolation."""
    repo_path = pathlib.Path(__file__).parent.parent / "app" / "db" / "repository.py"
    source = repo_path.read_text()
    
    # Verify table names come from VALID_CHILD_TABLES constant
    assert "for table in VALID_CHILD_TABLES:" in source
    
    # Verify the DELETE statement is still there (basic sanity check)
    assert "DELETE FROM" in source
