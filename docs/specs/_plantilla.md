# <ID>: <título corto de la tarea>

- **Paso de PROMPT_CHAIN.md:** <ID del paso, o "ninguno" si es una tarea de orquestación>
- **Fecha:** AAAA-MM-DD
- **Lecturas obligatorias:** `CLAUDE.md`; secciones de `INSTRUCCIONES.md` (por número); archivos existentes que hay que respetar.

## Objetivo

Qué debe existir al terminar y para qué sirve, en dos o tres frases. Sin detalles de implementación.

## Archivos

| Archivo | Acción | Contenido |
|---|---|---|
| `src/geass/...` | crear o modificar | qué va ahí |
| `tests/...` | crear | qué prueba |

Solo se tocan los archivos de esta tabla. Si hace falta tocar otro, el implementador se detiene y lo reporta.

## Interfaces

Firmas exactas, con tipos, de todo lo público que se crea o cambia. Incluye los errores que se lanzan y cuándo.

```python
def nombre(argumento: Tipo) -> TipoDeRetorno:
    """Qué hace, en una línea."""
```

Claves nuevas de `config/*.yaml`, con su unidad, si las hay. Dependencias nuevas, si las hay (van también a `docs/DECISIONES.md`, que edita el orquestador).

## Casos de prueba

Lista de tests que deben existir, con entradas y resultados concretos. Indica el marker de pytest si no es un test rápido (`hardware`, `windows_ui`, `slow`).

1. Dado ..., cuando ..., entonces ...

## Criterios de aceptación

Comprobables con un comando o leyendo el diff.

- [ ] `pytest -m "not hardware and not windows_ui and not slow"` pasa completo.
- [ ] `ruff check .` y `ruff format --check .` no reportan nada.
- [ ] ...

## Reglas de seguridad aplicables

Reglas de `CLAUDE.md` que esta tarea toca, copiadas o citadas, y cómo se cumplen aquí. Si no toca ninguna, escribe "Ninguna: la tarea no envía órdenes al brazo ni lee o actúa sobre la interfaz".

## Qué no hacer

- No implementar nada de pasos futuros.
- No cambiar interfaces existentes ni las definidas arriba.
- No añadir dependencias.
- No editar `PROMPT_CHAIN.md`, `docs/BITACORA.md`, `docs/DECISIONES.md` ni `docs/specs/`.
- ...

## Cambios

Vacío al crear la especificación. Si la tarea cambia después, se anota aquí con fecha y motivo.
