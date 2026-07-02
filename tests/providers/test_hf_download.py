from pathlib import Path

import click
import httpx
import pytest

from turbollm.hf_download import configured_path, translate_hf_errors


def _fake_response():
    return httpx.Response(404, request=httpx.Request("GET", "https://example.com"))


def test_configured_path_expands_user_and_returns_none_if_unset():
    model = {"local_path": "~/models/foo"}
    result = configured_path(model, "local_path")
    assert result == Path("~/models/foo").expanduser()
    assert configured_path({}, "local_path") is None
    assert configured_path({"local_path": ""}, "local_path") is None


def test_translate_hf_errors_wraps_gated_repo_error():
    from huggingface_hub.utils import GatedRepoError

    @translate_hf_errors
    def fails():
        raise GatedRepoError("nope", response=_fake_response())

    with pytest.raises(click.ClickException, match="gated"):
        fails()


def test_translate_hf_errors_wraps_http_error():
    from huggingface_hub.utils import HfHubHTTPError

    @translate_hf_errors
    def fails():
        raise HfHubHTTPError("network broke", response=_fake_response())

    with pytest.raises(click.ClickException, match="download failed"):
        fails()


def test_translate_hf_errors_passes_through_return_value():
    @translate_hf_errors
    def succeeds():
        return 42

    assert succeeds() == 42
