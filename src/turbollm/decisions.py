"""Local client for Kev's finite choice, yes/no, and ordered-score API."""

import json
import math
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import click

from turbollm.registry import resolve_model


def _reject_constant(value):
    raise ValueError(f"Invalid JSON number: {value}")


def _positive_timeout(ctx, param, value):
    if not math.isfinite(value) or value <= 0:
        raise click.BadParameter("must be a finite positive number", ctx=ctx, param=param)
    return value


def _validate(payload):
    if not isinstance(payload, dict) or "state" not in payload:
        raise click.ClickException("Request must be a JSON object containing state and questions")
    questions = payload.get("questions")
    if not isinstance(questions, dict) or not questions:
        raise click.ClickException("questions must be a nonempty object")
    for key, question in questions.items():
        if not isinstance(question, dict) or question.get("type") not in ("choice", "noul", "score"):
            raise click.ClickException(f"Question {key!r} must have type choice, noul, or score")
        kind = question["type"]
        criteria = question.get("criteria")
        if kind == "choice" and (not isinstance(criteria, dict) or not 1 <= len(criteria) <= 255):
            raise click.ClickException(f"Question {key!r}: choice criteria must contain 1..255 named options")
        if kind == "score" and (not isinstance(criteria, list) or not 1 <= len(criteria) <= 255):
            raise click.ClickException(f"Question {key!r}: score criteria must contain 1..255 ordered levels")
        if kind == "noul" and criteria is not None and not isinstance(criteria, dict):
            raise click.ClickException(f"Question {key!r}: noul criteria must be an object")


@click.command(name="decide", help="Answer typed decisions locally from a request JSON file, or '-' for stdin.")
@click.argument("request_file", type=click.File("r"), default="-")
@click.option("--model", default="kev-4b", show_default=True)
@click.option("--port", default=8901, type=click.IntRange(1, 65535), show_default=True)
@click.option("--timeout", default=60.0, type=float, callback=_positive_timeout, show_default=True,
              help="HTTP request timeout in seconds after the server is ready.")
def decide_cmd(request_file, model, port, timeout):
    try:
        payload = json.load(request_file, parse_constant=_reject_constant)
    except (ValueError, OSError) as exc:
        raise click.ClickException(f"Cannot read request JSON: {exc}") from exc
    _validate(payload)
    selected = resolve_model(model)
    if selected.get("backend") != "kev":
        raise click.ClickException("decide requires a model with backend 'kev'")
    # Caller-selected hosted/chat labels must never alter the local model chosen
    # through Turbo's registry and verified by the server lifecycle.
    payload = {**payload, "model": "kev-latest"}
    try:
        outgoing = json.dumps(payload, allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise click.ClickException(f"Invalid request JSON: {exc}") from exc

    def send(_model, serving_port):
        request = Request(f"http://127.0.0.1:{serving_port}/v1/systemone",
                          data=outgoing,
                          headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=timeout) as response:
                body = json.load(response, parse_constant=_reject_constant)
            if not isinstance(body, dict):
                raise ValueError("response must be a JSON object")
            rendered = json.dumps(body, allow_nan=False)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise click.ClickException(f"Kev returned HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise click.ClickException(f"Kev request failed: {exc}") from exc
        click.echo(rendered)

    from turbollm.cli import _run_with_server
    _run_with_server(selected, port, send)
