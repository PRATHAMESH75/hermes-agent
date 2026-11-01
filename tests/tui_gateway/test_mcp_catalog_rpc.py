"""The ``mcp.catalog`` RPC reports what each real catalog manifest declares."""

from __future__ import annotations

from hermes_cli.mcp_catalog import list_catalog
from tui_gateway import server


def test_catalog_rows_carry_each_manifests_transport_and_required_env():
    """Clients compare ``transport`` to "http"/"stdio" and prompt for the keys in ``requires``."""
    resp = server._methods["mcp.catalog"](1, {})
    rows = {row["name"]: row for row in resp["result"]["servers"]}

    entries = list_catalog()
    assert any(env.required for e in entries for env in e.auth.env)
    for entry in entries:
        assert rows[entry.name]["transport"] == entry.transport.type
        assert rows[entry.name]["requires"] == [env.name for env in entry.auth.env if env.required]
