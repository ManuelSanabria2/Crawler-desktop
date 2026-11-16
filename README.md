# GEASS

IA personal de Manuel Sanabria. Monorepo con dos componentes que comparten núcleo, configuración y herramientas:

- **arm**: la IA que controla un brazo robótico de 6 ejes que juega ajedrez. El PC ve el tablero, decide la jugada y planea el movimiento; el ESP32 solo ejecuta.
- **crawler**: el explorador del escritorio de Windows. Mapea las aplicaciones con UI Automation, construye un grafo de estados y acciones, y permite operarlas de forma segura.

Estado: esqueleto del repositorio. Todavía no hay lógica implementada.

## Documentos

- `INSTRUCCIONES.md`: especificación completa.
- `PROMPT_CHAIN.md`: plan de trabajo paso a paso.
- `CLAUDE.md`: reglas de trabajo y de seguridad.
- `docs/BITACORA.md`, `docs/DECISIONES.md`, `docs/PRUEBAS_HARDWARE.md`.

## Instalación

Windows 10 u 11 con Python 3.11.

```
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[arm,crawler,dev]"
```

El grupo `crawler` solo funciona en Windows. El grupo `arm` también funciona en Linux, para entrenar en Colab. Stockfish se instala aparte, como ejecutable.

## Comandos básicos

```
pytest                                    # tests rápidos
pytest -m "not hardware and not windows_ui and not slow"
ruff check . ; ruff format --check .

python -m geass.arm --help
python -m geass.crawler --help

cd firmware/esp32 ; pio run               # compilar firmware (cuando exista)
```

## Configuración

Todo valor ajustable vive en `config/*.yaml`, en unidades SI. Los datos de hardware que faltan están como `TODO` y el código debe fallar con un error claro si los necesita. `config/arm.test.yaml` contiene valores ficticios, solo para tests.

`data/` y `models/` quedan fuera de git: contienen información personal.
