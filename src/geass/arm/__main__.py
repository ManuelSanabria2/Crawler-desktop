"""CLI del brazo de GEASS. Por ahora solo muestra la ayuda."""

import typer

app = typer.Typer(
    help="IA del brazo robótico de ajedrez de GEASS.",
    add_completion=False,
)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """IA del brazo robótico de ajedrez de GEASS. Todavía no hay comandos."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


if __name__ == "__main__":
    app(prog_name="python -m geass.arm")
