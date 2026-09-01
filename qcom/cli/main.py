"""Command line: run, resume, smoke, health, template. Thin shell over core; exit codes only."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from qcom import __version__
from qcom.core.clock import now_utc
from qcom.core.config import AppConfig, load_config
from qcom.core.errors import ConfigError, InputValidationError
from qcom.core.logging import configure_logging, get_logger
from qcom.core.summary import EXIT_ABORTED, EXIT_INPUT_INVALID, render_text

app = typer.Typer(add_completion=False, no_args_is_help=True, pretty_exceptions_enable=False, help=f"qcom {__version__}: quick-commerce price and availability scraper")


def _load(config: Path, headed: bool) -> AppConfig:
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        typer.echo(f"config error: {exc}", err=True)
        raise typer.Exit(EXIT_INPUT_INVALID)
    if headed:
        cfg.browser.headless = False
    return cfg


def _finish(cfg: AppConfig, run_id: str, summary, out_dir: Path) -> None:
    from qcom.core.storage import Storage
    from qcom.io.excel_out import write_workbook

    out_path = out_dir / f"{run_id}_results.xlsx"
    with Storage(cfg.storage.path) as storage:
        write_workbook(storage, run_id, out_path)
    typer.echo(render_text(summary, str(out_path)))
    raise typer.Exit(summary.exit_code)


@app.command()
def run(
    input: Path = typer.Option(..., "--input", "-i", help="input workbook: sheet 1 products, sheet 2 pincodes"),
    out: Path = typer.Option(Path("output"), "--out", "-o", help="directory for <run_id>_results.xlsx"),
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    platforms: str | None = typer.Option(None, "--platforms", "-p", help="comma-separated; overrides the settings sheet"),
    max_results: int | None = typer.Option(None, "--max-results", help="overrides the settings sheet"),
    label: str | None = typer.Option(None, "--label", help="run_label written to run_meta"),
    headed: bool = typer.Option(False, "--headed", help="visible browser, for debugging"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Validate the workbook, plan every job, run them, write the output workbook."""
    from qcom.core.runner import execute_run, plan_run
    from qcom.io.excel_in import read_input

    cfg = _load(config, headed)
    configure_logging(None, verbose=verbose)
    try:
        spec = read_input(input)
    except InputValidationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(EXIT_INPUT_INVALID)
    started = now_utc()
    try:
        run_id = plan_run(
            cfg, spec,
            platforms=[s for s in platforms.split(",")] if platforms else None,
            max_results=max_results, label=label,
        )
    except (KeyError, ValueError) as exc:
        typer.echo(f"cannot plan run: {exc}", err=True)
        raise typer.Exit(EXIT_INPUT_INVALID)
    configure_logging(Path(cfg.storage.runs_dir) / run_id / "run.jsonl", verbose=verbose)
    get_logger(__name__).info("run.begin", run_id=run_id, input=str(input), products=len(spec.products), pincodes=len(spec.pincodes))
    summary = execute_run(cfg, run_id, started_at=started)
    _finish(cfg, run_id, summary, out)


@app.command()
def resume(
    run_id: str = typer.Option(..., "--run-id"),
    out: Path = typer.Option(Path("output"), "--out", "-o"),
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    headed: bool = typer.Option(False, "--headed"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Finish an interrupted run: skips completed jobs, never duplicates rows."""
    from qcom.core.runner import resume_run

    cfg = _load(config, headed)
    configure_logging(Path(cfg.storage.runs_dir) / run_id / "run.jsonl", verbose=verbose)
    try:
        summary = resume_run(cfg, run_id)
    except KeyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(EXIT_INPUT_INVALID)
    _finish(cfg, run_id, summary, out)


@app.command()
def smoke(
    platform: str = typer.Option(..., "--platform"),
    pincode: str = typer.Option(..., "--pincode"),
    term: str = typer.Option(..., "--term"),
    max_results: int = typer.Option(10, "--max-results"),
    city: str | None = typer.Option(None, "--city"),
    save_captures: Path | None = typer.Option(None, "--save-captures", help="directory to write every raw capture body into, for building parser fixtures"),
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    headed: bool = typer.Option(False, "--headed"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """One live query. Prints the readback, the strategy and the rows. Persists nothing."""
    from qcom.core.browser import BrowserManager
    from qcom.core.location import make_expectation
    from qcom.core.models import Job
    from qcom.core.quality import finalise_listings
    from qcom.core.runner import NoBrowserPage
    from qcom.platforms.registry import get_adapter_class

    cfg = _load(config, headed)
    configure_logging(None, verbose=verbose)
    try:
        adapter = get_adapter_class(platform)(navigation_timeout_s=cfg.browser.navigation_timeout_s)
    except KeyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(EXIT_INPUT_INVALID)

    browser = None
    handle = None
    try:
        if adapter.needs_browser:
            browser = BrowserManager(cfg.browser, cfg.proxy, Path(cfg.storage.sessions_dir))
            browser.start()
            handle = browser.new_context(platform, pincode, use_jar=True)
            page = handle.page
        else:
            page = NoBrowserPage(pincode)
        loc = adapter.set_location(page, pincode, make_expectation(pincode, city))
        typer.echo(f"location: effective_pincode={loc.effective_pincode} store={loc.store_id} eta={loc.eta_minutes} address={loc.address_text!r}")
        if loc.effective_pincode != pincode:
            typer.echo("readback does not carry the requested pincode; refusing to search", err=True)
            raise typer.Exit(EXIT_ABORTED)
        captures = adapter.search(page, term, max_results)
        for i, c in enumerate(captures, start=1):
            c.capture_id = f"smoke:{i:03d}"
            typer.echo(f"capture {i}: strategy={c.strategy} source={c.source.value} url={c.url} status={c.http_status} bytes={c.size_bytes} parse={c.parse}")
        if save_captures is not None:
            typer.echo(f"captures written to {_dump_captures(save_captures, captures)}")
        parsed = [(c, adapter.parse(c)) for c in captures if c.parse]
        job = Job(job_id="smoke", run_id="smoke", platform=platform, requested_pincode=pincode, search_term=term, input_row_id=2, pincode_row_id=2, max_results=max_results)
        rows, events = finalise_listings(job, parsed, loc)
        typer.echo(f"rows: {len(rows)}  data quality events: {len(events)}")
        for r in rows:
            sp = None if r.selling_price_paise is None else r.selling_price_paise / 100
            mrp = None if r.mrp_paise is None else r.mrp_paise / 100
            typer.echo(f"  #{r.result_rank:>2} {r.platform_product_id:<14} {r.product_name[:40]:<40} {str(r.pack_size):<12} sp={sp} mrp={mrp} in_stock={r.in_stock} qty={r.stock_qty} score={r.match_score}")
        for e in events:
            typer.echo(f"  dq: {e.kind}: {e.detail}")
    finally:
        if handle is not None and browser is not None:
            browser.close_context(handle)
        if browser is not None:
            browser.close()


def _dump_captures(directory: Path, captures: list) -> Path:
    """Raw bodies verbatim, one file each, plus an index. Header names only; never cookie values."""
    import json

    directory.mkdir(parents=True, exist_ok=True)
    index = []
    for i, c in enumerate(captures, start=1):
        ext = "json" if "json" in (c.content_type or "") else ("html" if "html" in (c.content_type or "") else "bin")
        name = f"{i:03d}_{c.strategy}.{ext}"
        (directory / name).write_bytes(c.body)
        index.append({"file": name, "strategy": c.strategy, "source": c.source.value, "url": c.url, "http_status": c.http_status, "content_type": c.content_type, "bytes": c.size_bytes, "sha256": c.sha256, "parse": c.parse, "request": c.request})
    (directory / "index.json").write_text(json.dumps(index, indent=1, default=str), encoding="utf-8")
    return directory


@app.command()
def health(
    platform: str | None = typer.Option(None, "--platform", help="one platform; default every implemented one"),
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    headed: bool = typer.Option(False, "--headed"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run each platform's known-good probe and assert the documented shape. Exits non-zero on drift."""
    from qcom.core.browser import BrowserManager
    from qcom.core.runner import NoBrowserPage
    from qcom.platforms.registry import get_adapter_class, implemented_platforms

    cfg = _load(config, headed)
    configure_logging(None, verbose=verbose)
    names = [platform] if platform else implemented_platforms()
    if not names:
        typer.echo("no real platform adapter is implemented yet; try --platform fake", err=True)
        raise typer.Exit(EXIT_INPUT_INVALID)
    failed = 0
    for name in names:
        try:
            adapter = get_adapter_class(name)(navigation_timeout_s=cfg.browser.navigation_timeout_s)
        except KeyError as exc:
            typer.echo(str(exc), err=True)
            failed += 1
            continue
        browser = None
        handle = None
        try:
            if adapter.needs_browser:
                browser = BrowserManager(cfg.browser, cfg.proxy, Path(cfg.storage.sessions_dir))
                browser.start()
                handle = browser.new_context(name, adapter.probe.pincode, use_jar=True)
                page = handle.page
            else:
                page = NoBrowserPage(adapter.probe.pincode)
            report = adapter.health_check(page)
        except Exception as exc:  # noqa: BLE001 - reported as a failed check, never hidden
            typer.echo(f"{name}: FAIL  {type(exc).__name__}: {exc}")
            failed += 1
            continue
        finally:
            if handle is not None and browser is not None:
                browser.close_context(handle)
            if browser is not None:
                browser.close()
        typer.echo(f"{name}: {'OK' if report.ok else 'DRIFT'}  adapter v{report.adapter_version} strategy={report.strategy}")
        for c in report.checks:
            typer.echo(f"  [{'ok' if c.ok else 'FAIL'}] {c.name}  {c.detail}")
        if not report.ok:
            failed += 1
    raise typer.Exit(1 if failed else 0)


@app.command()
def template(out: Path = typer.Option(Path("input.xlsx"), "--out", "-o")) -> None:
    """Write a blank input workbook with the right sheets and headers."""
    from qcom.io.template import write_template

    if out.exists():
        typer.echo(f"{out} already exists; refusing to overwrite", err=True)
        raise typer.Exit(EXIT_INPUT_INVALID)
    typer.echo(f"wrote {write_template(out)}")


def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(main())
