import pytest

from laip.masking import mask


def test_local_display_redacts_credentials_without_mutating_source():
    source = {
        "metadata": {"api_key": "private-value", "variable": "BALANCE"},
        "excerpt": "password=fixture-secret Bearer fixture-token postgresql://u:pass@host/db",
    }
    projected = mask(source)
    assert "private-value" not in str(projected)
    assert "fixture-secret" not in str(projected)
    assert "fixture-token" not in str(projected)
    assert "u:pass" not in str(projected)
    assert projected["metadata"]["variable"] == "BALANCE"
    assert source["metadata"]["api_key"] == "private-value"


def test_masking_resource_bounds():
    with pytest.raises(ValueError, match="MASKING_RESOURCE_LIMIT"):
        mask([1, 2], max_nodes=1)
