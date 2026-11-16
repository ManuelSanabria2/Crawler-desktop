"""CLI del crawler de GEASS."""

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import typer
from rich.console import Console

from geass.core.errors import GeassError

app = typer.Typer(
    help="Crawler del escritorio de Windows de GEASS.",
    add_completion=False,
)
console = Console(stderr=True)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Crawler del escritorio de Windows de GEASS."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


@app.command()
def dump(
    active: Annotated[
        bool, typer.Option("--active", help="Vuelca la ventana en primer plano.")
    ] = False,
    handle: Annotated[
        int | None, typer.Option("--handle", help="Handle de la ventana a volcar.")
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Archivo JSON de salida. Sin esta opción se imprime."),
    ] = None,
    delay: Annotated[
        float,
        typer.Option(
            "--delay", help="Segundos de espera antes de volcar, para cambiar de ventana."
        ),
    ] = 0.0,
) -> None:
    """Vuelca el árbol de UI Automation de una ventana. Solo lee, nunca actúa."""
    if active == (handle is not None):
        console.print("[red]Indica --active o --handle, uno de los dos.[/red]")
        raise typer.Exit(code=2)

    from geass.crawler.uia import dump_window

    if delay > 0:
        time.sleep(delay)
    try:
        result = dump_window(None if active else handle)
    except GeassError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1) from error

    text = result.model_dump_json(indent=2)
    if out is not None:
        out.write_text(text, encoding="utf-8")
    else:
        typer.echo(text)

    status = "parcial: " + ", ".join(result.partial_reasons) if result.partial else "completo"
    console.print(
        f"{result.process_name} | {result.node_count} nodos | "
        f"profundidad {result.max_depth_reached} | {result.elapsed_s * 1000:.0f} ms | {status}"
    )
    if out is not None:
        console.print(f"Guardado en {out}")


@app.command()
def compare(
    a: Annotated[Path, typer.Argument(help="Volcado A, en JSON (salida de dump --out).")],
    b: Annotated[Path, typer.Argument(help="Volcado B, en JSON.")],
    max_lines: Annotated[
        int, typer.Option("--max-lines", help="Máximo de diferencias listadas por grupo.")
    ] = 15,
) -> None:
    """Compara dos volcados y explica qué diferencias afectan a cada firma."""
    from pydantic import ValidationError

    from geass.crawler.signature import compare_dumps, explain
    from geass.crawler.uia import WindowDump

    dumps = []
    for path in (a, b):
        try:
            dumps.append(WindowDump.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValidationError) as error:
            console.print(f"[red]No se pudo leer el volcado {path}: {error}[/red]")
            raise typer.Exit(code=1) from error
    try:
        comparison = compare_dumps(dumps[0], dumps[1])
    except GeassError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1) from error
    for line in explain(comparison, max_lines=max_lines):
        typer.echo(line)


DbOption = Annotated[
    Path | None,
    typer.Option("--db", help="Base del crawler. Por defecto, store.db_path de crawler.yaml."),
]


@app.command()
def query(
    text: Annotated[str, typer.Argument(help="Texto a buscar entre los nombres de elementos.")],
    app_name: Annotated[str, typer.Option("--app", help="App por ejecutable o nombre visible.")],
    from_state: Annotated[
        int | None,
        typer.Option("--from", help="Id del estado actual: describe la ruta hasta el elemento."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Máximo de coincidencias.")] = 10,
    db: DbOption = None,
) -> None:
    """Busca un elemento en lo ya mapeado de una app y, con --from, describe la ruta.

    Solo lee la base: no ejecuta ninguna acción de la ruta.
    """
    from geass.core.config import load_crawler_config
    from geass.crawler.graph import CrawlerGraph
    from geass.crawler.store import CrawlerStore

    try:
        config = load_crawler_config()
        with CrawlerStore(_existing_db(db, config.store.db_path)) as store:
            graph = CrawlerGraph.from_store(store, config)
        record = graph.resolve_app(app_name)
        matches = graph.find_element(app_name, text, limit=limit)
        if not matches:
            typer.echo(f"Sin coincidencias para '{text}' en {record.executable}.")
            raise typer.Exit(code=1)
        for match in matches:
            element = match.element
            typer.echo(
                f'{match.score:.1f}  "{element.name}" ({element.control_type})  '
                f"estado {match.state_id}: {match.state_title}"
            )
        if from_state is not None:
            route = graph.path_to(app_name, from_state, text)
            typer.echo("")
            typer.echo(f"Ruta desde el estado {from_state}:")
            for line in route.describe():
                typer.echo(line)
    except (GeassError, ValueError) as error:
        _error(str(error))
        raise typer.Exit(code=1) from error


@app.command()
def coverage(
    app_name: Annotated[
        str | None, typer.Option("--app", help="Solo esta app. Sin la opción, todas.")
    ] = None,
    db: DbOption = None,
) -> None:
    """Muestra cuánto de cada app está mapeado en la base."""
    from geass.core.config import load_crawler_config
    from geass.crawler.graph import CrawlerGraph
    from geass.crawler.store import CrawlerStore

    try:
        config = load_crawler_config()
        with CrawlerStore(_existing_db(db, config.store.db_path)) as store:
            graph = CrawlerGraph.from_store(store, config)
        entries = graph.coverage(app_name)
    except GeassError as error:
        _error(str(error))
        raise typer.Exit(code=1) from error
    if not entries:
        typer.echo("La base no tiene ninguna app registrada.")
        return
    for index, entry in enumerate(entries):
        if index:
            typer.echo("")
        typer.echo(f"{entry.app.display_name} ({entry.app.executable})")
        lines = (
            ("Estados", str(entry.states)),
            ("Visitas", str(entry.visits)),
            ("Elementos", str(entry.elements)),
            ("Elementos accionables", str(entry.actionable_elements)),
            ("Elementos ejercitados", str(entry.exercised_elements)),
            ("Cobertura de elementos", f"{entry.element_coverage:.0%}"),
            ("Transiciones", str(entry.transitions)),
            ("Observaciones", str(entry.observations)),
            ("Transiciones pasivas", str(entry.passive_transitions)),
            ("Transiciones activas", str(entry.active_transitions)),
            ("Transiciones ejecutables", str(entry.executable_transitions)),
            ("Transiciones sin elemento", str(entry.transitions_without_element)),
            ("Estados aislados", str(entry.isolated_states)),
            ("Estados sin salida ejecutable", str(entry.dead_end_states)),
        )
        for label, value in lines:
            typer.echo(f"  {label}: {value}")


_EXPORT_LABELS = {
    "created": "creada",
    "updated": "actualizada",
    "unchanged": "sin cambios",
    "error": "error",
}
_DRY_RUN_LABELS = {**_EXPORT_LABELS, "created": "se crearía", "updated": "se actualizaría"}


@app.command()
def export(
    app_name: Annotated[
        str | None, typer.Option("--app", help="Solo esta app. Sin la opción, todas.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Muestra qué notas cambiarían sin escribir nada.")
    ] = False,
    vault: Annotated[
        Path | None,
        typer.Option(
            "--vault",
            help="Bóveda de Obsidian. Por defecto, export.obsidian_vault_path de crawler.yaml.",
        ),
    ] = None,
    db: DbOption = None,
) -> None:
    """Escribe o actualiza la nota de cada app en la bóveda de Obsidian.

    Solo reescribe el bloque administrado de cada nota: lo demás se conserva tal cual.
    """
    from geass.core.config import load_crawler_config
    from geass.crawler.export import export_notes
    from geass.crawler.graph import CrawlerGraph
    from geass.crawler.store import CrawlerStore

    if dry_run:
        typer.echo("Simulación: no se escribe nada.")
    try:
        config = load_crawler_config()
        if vault is None:
            # Si la bóveda sigue como TODO, falla aquí, antes de abrir la base.
            _ = config.export.obsidian_vault_path
        with CrawlerStore(_existing_db(db, config.store.db_path)) as store:
            graph = CrawlerGraph.from_store(store, config)
        if not graph.apps():
            typer.echo("La base no tiene ninguna app registrada.")
            return
        results = export_notes(graph, config, app=app_name, vault=vault, dry_run=dry_run)
    except GeassError as error:
        _error(str(error))
        raise typer.Exit(code=1) from error

    labels = _DRY_RUN_LABELS if dry_run else _EXPORT_LABELS
    for result in results:
        if result.status == "skipped_excluded":
            typer.echo(f"omitida (app excluida): {result.app.executable}")
        elif result.status == "error":
            typer.echo(f"{labels['error']}: {result.path} ({result.detail})")
        else:
            typer.echo(f"{labels[result.status]}: {result.path}")
    if any(result.status == "error" for result in results):
        raise typer.Exit(code=1)


def _existing_db(db: Path | None, default: str) -> Path:
    """Ruta de la base que ya existe. Nunca la crea: si falta, lanza GeassError."""
    from geass.core.paths import resolve_project_path

    path = db if db is not None else resolve_project_path(default)
    if not path.is_file():
        raise GeassError(f"La base todavía no existe: {path}")
    return path


def _error(message: str) -> None:
    console.print(message, style="red", markup=False, highlight=False, soft_wrap=True)


passive_app = typer.Typer(
    help="Crawler pasivo: observa las apps que usas y registra estados y transiciones. "
    "Solo lee: nunca mueve el mouse ni pulsa teclas.",
    add_completion=False,
    no_args_is_help=True,
)
app.add_typer(passive_app, name="passive")


@passive_app.command("start")
def passive_start(
    duration: Annotated[
        float | None,
        typer.Option(
            "--duration", help="Segundos que corre antes de parar solo. Sin esta opción, sin fin."
        ),
    ] = None,
) -> None:
    """Arranca el crawler pasivo en esta terminal hasta Ctrl+C o 'passive stop'."""
    if duration is not None and duration <= 0:
        console.print("[red]--duration debe ser mayor que 0.[/red]")
        raise typer.Exit(code=2)

    from geass.core.config import load_crawler_config
    from geass.core.paths import resolve_project_path
    from geass.crawler.passive.runner import PassiveRunner

    try:
        config = load_crawler_config()
        console.print(f"Atajo de pausa y reanudación: {config.passive.pause_hotkey}")
        console.print(f"Base: {resolve_project_path(config.store.db_path)}")
        console.print(
            "Para parar: Ctrl+C en esta terminal, o 'python -m geass.crawler passive stop' "
            "desde otra."
        )
        final = PassiveRunner(config).run(duration_s=duration)
    except GeassError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1) from error

    counters = final.counters
    budget = "dentro del presupuesto" if final.within_cpu_budget else "fuera del presupuesto"
    console.print(f"Duración: {final.wall_s:.1f} s")
    console.print(f"Estados registrados: {_count(counters.get('states_recorded', 0))}")
    console.print(f"Estados nuevos: {_count(counters.get('states_new', 0))}")
    console.print(f"Transiciones: {_count(counters.get('transitions_recorded', 0))}")
    console.print(f"Clics vistos: {_count(counters.get('presses_seen', 0))}")
    console.print(
        f"CPU: {final.cpu_fraction:.2%} de un núcleo, {final.cpu_fraction_machine:.2%} de la "
        f"máquina; {budget} ({final.cpu_budget_fraction:.0%} de un núcleo)."
    )


@passive_app.command("stop")
def passive_stop() -> None:
    """Pide la parada al crawler pasivo que corre en otra terminal."""
    from geass.crawler.passive.runner import request_stop

    try:
        requested = request_stop()
    except GeassError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1) from error
    if not requested:
        console.print("No hay ningún crawler pasivo en marcha.")
        raise typer.Exit(code=1)
    console.print("Se pidió la parada del crawler pasivo.")


@passive_app.command("stats")
def passive_stats() -> None:
    """Muestra el último estado del crawler pasivo y los conteos de la base."""
    from geass.core.config import load_crawler_config
    from geass.core.paths import resolve_project_path
    from geass.crawler.passive.runner import read_status
    from geass.crawler.store import CrawlerStore

    try:
        config = load_crawler_config()
        status = read_status(config)
        db_path = resolve_project_path(config.store.db_path)
        counts = None
        if db_path.is_file():
            with CrawlerStore(db_path) as store:
                counts = store.counts()
    except GeassError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1) from error

    if status is None:
        typer.echo("No hay archivo de estado: el crawler pasivo no se ha ejecutado todavía.")
    else:
        state = f"en marcha (pid {status.pid})" if status.running else "detenido"
        if status.running and status.paused:
            state += ", en pausa"
        budget = "dentro" if status.within_cpu_budget else "fuera"
        typer.echo(f"Estado: {state}")
        typer.echo(f"Sesión: {status.session_id}")
        typer.echo(f"Desde: {_when(status.started_at)}")
        typer.echo(f"Última actualización: {_when(status.updated_at)}")
        typer.echo(f"Duración medida: {status.wall_s:.1f} s")
        typer.echo(
            f"CPU: {status.cpu_fraction:.2%} de un núcleo, "
            f"{status.cpu_fraction_machine:.2%} de la máquina; {budget} del presupuesto "
            f"({status.cpu_budget_fraction:.0%} de un núcleo)."
        )
        nonzero = {name: value for name, value in status.counters.items() if value}
        if nonzero:
            typer.echo("Contadores:")
            for name, value in nonzero.items():
                typer.echo(f"  {name}: {_count(value)}")
        else:
            typer.echo("Contadores: todos en cero.")

    if counts is None:
        typer.echo(f"La base todavía no existe: {db_path}")
    else:
        typer.echo(f"Base {db_path}:")
        for table, number in counts.items():
            typer.echo(f"  {table}: {number}")


def _count(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.3f}"


def _when(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


do_app = typer.Typer(
    help="Acciones directas por orden tuya: abrir apps, el explorador y teclas multimedia.",
    add_completion=False,
    no_args_is_help=True,
)
app.add_typer(do_app, name="do")

# Quién autoriza las acciones que se piden desde esta CLI. Queda en la auditoría.
_CLI_AUTHORIZER = "cli:manuel"

# Las mismas acciones que geass.crawler.actions.MediaAction. Se repiten aquí para que la
# CLI no importe el paquete de acciones fuera de los comandos.
MediaChoice = Literal["play_pause", "next", "previous", "volume_up", "volume_down", "mute"]

RefreshOption = Annotated[
    bool, typer.Option("--refresh", help="Reconstruye el índice de apps antes de usarlo.")
]


@do_app.command("launch")
def do_launch(
    name: Annotated[str, typer.Argument(help="Nombre aproximado de la app.")],
    refresh: RefreshOption = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Dice qué app se abriría, sin abrirla.")
    ] = False,
) -> None:
    """Abre una app del menú Inicio por su nombre aproximado. Ante la duda no abre nada."""
    from geass.core.config import load_crawler_config, load_safety_config
    from geass.crawler.actions import AppIndex, AuditLog, launch

    try:
        config = load_crawler_config()
        safety = load_safety_config()
        index = AppIndex.load(config, safety, refresh=refresh)
        result = launch(
            name,
            authorized_by=_CLI_AUTHORIZER,
            index=index,
            config=config,
            safety=safety,
            audit=AuditLog.from_config(safety),
            dry_run=dry_run,
        )
    except (GeassError, ValueError) as error:
        _error(str(error))
        raise typer.Exit(code=1) from error

    if result.entry is not None and result.status == "launched":
        typer.echo(f"Abriendo {result.entry.name}.")
    elif result.entry is not None and result.status == "would_launch":
        typer.echo(f"Se abriría {result.entry.name}.")
    elif result.entry is not None and result.status == "blocked":
        typer.echo(
            f"{result.entry.name} coincide con la lista de bloqueo de safety.yaml y no se abre."
        )
        raise typer.Exit(code=1)
    elif result.status == "ambiguous":
        typer.echo(f"'{result.query}' es ambiguo. Opciones:")
        _echo_options(result.options)
        raise typer.Exit(code=2)
    else:
        typer.echo(f"No hay ninguna app que coincida con '{result.query}'.")
        if result.options:
            typer.echo("Parecidas:")
            _echo_options(result.options)
        raise typer.Exit(code=1)


def _echo_options(options: list[Any]) -> None:
    for number, option in enumerate(options, start=1):
        typer.echo(f"  {number}. {option.entry.name} ({option.score:.1f})")


@do_app.command("media")
def do_media(
    action: Annotated[MediaChoice, typer.Argument(help="Tecla multimedia que se pulsa.")],
) -> None:
    """Pulsa una tecla multimedia: reproducir o pausar, siguiente, anterior o volumen."""
    from geass.core.config import load_crawler_config, load_safety_config
    from geass.crawler.actions import AuditLog, media

    try:
        config = load_crawler_config()
        audit = AuditLog.from_config(load_safety_config())
        media(action, authorized_by=_CLI_AUTHORIZER, config=config, audit=audit)
    except (GeassError, ValueError) as error:
        _error(str(error))
        raise typer.Exit(code=1) from error
    typer.echo(f"Hecho: {action}.")


@do_app.command("explorer")
def do_explorer(
    path: Annotated[
        str | None, typer.Argument(help="Carpeta que se abre. Sin ella, el explorador sin más.")
    ] = None,
) -> None:
    """Abre el explorador de archivos, opcionalmente en una carpeta que existe."""
    from geass.core.config import load_crawler_config, load_safety_config
    from geass.crawler.actions import AuditLog, open_explorer

    try:
        config = load_crawler_config()
        audit = AuditLog.from_config(load_safety_config())
        folder = open_explorer(path, authorized_by=_CLI_AUTHORIZER, config=config, audit=audit)
    except (GeassError, ValueError) as error:
        _error(str(error))
        raise typer.Exit(code=1) from error
    typer.echo("Explorador abierto." if folder is None else f"Explorador abierto en {folder}.")


@do_app.command("apps")
def do_apps(refresh: RefreshOption = False) -> None:
    """Lista las apps del índice que se pueden abrir con 'do launch'."""
    from geass.core.config import load_crawler_config, load_safety_config
    from geass.crawler.actions import AppIndex

    try:
        index = AppIndex.load(load_crawler_config(), load_safety_config(), refresh=refresh)
    except (GeassError, ValueError) as error:
        _error(str(error))
        raise typer.Exit(code=1) from error
    entries = index.entries
    typer.echo(f"{len(entries)} apps en el índice (construido el {_when(index.built_at)}).")
    for entry in entries:
        typer.echo(f"{entry.name} [bloqueada]" if entry.blocked else entry.name)


if __name__ == "__main__":
    app(prog_name="python -m geass.crawler")
