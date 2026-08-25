from pathlib import Path


def test_omp_autoserve_extension_is_lazy_and_non_destructive():
    source = (
        Path(__file__).parent.parent
        / "integrations"
        / "omp"
        / "turbo-autoserve.ts"
    ).read_text()

    assert 'pi.on("before_provider_request"' in source
    assert 'model.provider !== "turbo"' in source
    assert 'spawn("turbo", ["serve"' in source
    assert "detached: true" in source
    assert "server is already serving" in source
    assert ".kill(" not in source
