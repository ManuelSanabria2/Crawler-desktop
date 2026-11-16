# Bitácora

Una entrada por paso terminado de `PROMPT_CHAIN.md`. La más reciente va arriba.

## Plantilla

```
## AAAA-MM-DD. Paso <id>: <título>

**Qué se hizo**
- ...

**Verificaciones**
- comando y resultado

**Decisiones**
- ... (el detalle va en DECISIONES.md)

**Pendientes**
- ...
```

## 2026-11-14. Paso C07: lanzador y acciones directas (geass.crawler.actions), modo D

**Qué se hizo**
- El orquestador escribió `docs/specs/C07.md`. Todo el código lo escribió el subagente `implementer`:
  - `actions/system.py`: las cinco únicas funciones que tocan el sistema, y `MEDIA_KEYS`.
  - `actions/index.py`: `AppIndex` y `AppEntry`, con caché.
  - `actions/launcher.py`: `match_app` y `launch`.
  - `actions/direct.py`: `open_explorer` y `media`.
  - `actions/audit.py`: `AuditLog`. `actions/errors.py`: `ActionError` y derivados.
  - CLI: `python -m geass.crawler do launch NOMBRE [--refresh] [--dry-run]`, `do media ACCION`, `do explorer [RUTA]` y `do apps [--refresh]`.
  - Cinco claves nuevas en la sección `launcher` de `crawler.yaml` y su esquema.
  - Tests en `tests/crawler/actions/`, con un fixture automático que sustituye las cinco funciones del sistema en todos los tests rápidos.

**Revisión del orquestador**
- Huella de hashes: 147 archivos antes, 161 después. Cambiaron solo los archivos de la tabla de la especificación. En `crawler.yaml` y en el esquema el cambio son exactamente las cinco claves nuevas. En `__main__.py` solo cambió una línea de importación, además de lo añadido.
- Se leyeron completos `system.py` y `launcher.py`, que son los que deciden y ejecutan. `index.py`, `direct.py`, `audit.py`, los comandos de la CLI y los tests no se leyeron línea a línea: se comprobaron con los tests y con simulaciones.
- `SendInput`, `startfile`, `Popen` y `subprocess` solo aparecen en `system.py`. Ningún archivo de `passive/`, `graph/` ni `export/` importa `geass.crawler.actions`.
- Los casos de coincidencia de la especificación se comprobaron con rapidfuzz antes de delegar.
- Desviaciones del subagente, todas del lado conservador y aceptadas: dos coincidencias exactas dan `ambiguous`; `AppEntry` valida que cada fuente traiga su dato; una caché inconsistente o con fecha futura se reconstruye; los errores de validación de `open_explorer` y `media` también se auditan; `ActionBlockedError` está definida pero nada la lanza (el bloqueo se devuelve como estado).
- No hubo ronda de correcciones.

**Verificaciones**
- `.venv\Scripts\python -m pytest -m "not hardware and not windows_ui and not slow"`: 521 pasan (448 anteriores más 73 nuevos), 1 omitido (el de la bóveda sin configurar), 9 excluidos por marker.
- `.venv\Scripts\python -m ruff check .` y `ruff format --check .`: sin errores, 128 archivos.
- Los dos tests `windows_ui` nuevos se recogen sin error de importación. No se ejecutaron.
- `do apps`: 178 apps en el índice real; creó `data/crawler/app_index.json`. Cinco quedan bloqueadas: tres desinstaladores, "System Information" y una referencia de RivaTuner.
- Simulaciones (`do launch ... --dry-run`) con el índice real:
  - "bloc de nots": Bloc de notas. "calculadra": Calculadora. "paint": Paint. "recortes": Herramienta Recortes. "configuracion": Configuración. "obsidian": Obsidian. "visual studio cod": Visual Studio Code.
  - "spotfy": no encontrada, con sugerencias. Spotify no está en el índice de este PC.
- Ninguna acción real se ha ejecutado todavía: `data/crawler/audit.jsonl` no existe.

**Decisiones**
- D015 en `DECISIONES.md`.

**Pendientes**
- Verificación real, hecha por el orquestador el 2026-11-14 con el visto bueno de Manuel, desde la CLI:
  - `do launch` con "bloc de nots", "calculadra", "paint", "recortes" y "configuracion": las cinco respondieron "Abriendo ..." con código 0.
  - Comprobado contando ventanas visibles antes y después: Bloc de notas, Calculadora, Paint y Herramienta Recortes pasaron de 0 a 1 ventana. Son 4 de 5 confirmadas.
  - Configuración no se pudo confirmar así: ya había 2 ventanas que coincidían con el patrón antes de lanzar y siguió habiendo 2. No se sabe si se abrió, si se trajo al frente una que ya estaba, o si el patrón contaba otras ventanas.
  - `do explorer`: las ventanas del explorador pasaron de 3 a 4.
  - `do media play_pause` dos veces, con 4 s entre una y otra: Windows aceptó las dos pulsaciones (código 0). Si la música se pausó y se reanudó solo lo puede decir Manuel; no se comprobó de otra forma.
  - `data/crawler/audit.jsonl` tiene las 8 líneas: 5 `launch` con resultado `launched`, 1 `open_explorer` y 2 `media` con resultado `done`, todas con `cli:manuel`.
  - Las ventanas abiertas se dejaron abiertas: este paso no cierra apps.
- Respuesta de Manuel sobre lo que no se pudo confirmar:
  - Configuración no se abrió en ese primer intento, aunque la CLI dijo "Abriendo Configuración" y la auditoría dice `launched`. Las 2 ventanas que contaba el patrón no eran de Configuración: al mirar después, la única coincidencia era un Bloc de notas con "settings" en el título, y `SystemSettings` no estaba en marcha.
  - Un segundo `do launch configuracion`, unos minutos después, sí la abrió: `SystemSettings` arrancó y hay una ventana "Configuración". El fallo es intermitente y su causa no se encontró. La entrada del índice es correcta (`start_apps`, el mismo mecanismo `shell:AppsFolder` con el que se abrieron Calculadora y Paint). Diferencia entre los dos intentos: el primero fue 3 s después de otro lanzamiento y el segundo fue solo.
  - Conclusión: `launch` puede decir `launched` sin que la app se abra, porque no comprueba nada después de pedirle la apertura a Windows. La auditoría registra la orden, no el efecto.
  - Música: no había nada reproduciéndose. Manuel usa TIDAL y no estaba abierto, así que las dos pulsaciones de `play_pause` no prueban nada. TIDAL está en el índice (`do launch tidal --dry-run`: "Se abriría TIDAL").
- Música, segunda prueba el mismo día: `do launch tidal` abrió TIDAL (proceso y ventana comprobados), Manuel puso música y se envió `do media play_pause` dos veces con 5 s de separación. Manuel confirmó que TIDAL se pausó y volvió a sonar. Las teclas multimedia funcionan sin `KEYEVENTF_EXTENDEDKEY`.
- Resultado de la verificación pedida por el paso, cumplida: 6 apps abiertas por nombre aproximado (Bloc de notas, Calculadora, Paint, Herramienta Recortes, Configuración al segundo intento y TIDAL), explorador abierto, y música pausada y reanudada.
- Los dos tests `windows_ui` de lanzamiento no se corrieron: abren Bloc de notas y Calculadora, lo mismo que ya cubrió la verificación manual.
- De las seis acciones multimedia solo se probó `play_pause` en real; las otras cinco usan el mismo camino con otro código de tecla.
- Para el paso del ejecutor (sección 6.9, "verificación posterior"): comprobar que la app apareció y reintentar una vez. Hasta entonces, "Abriendo ..." significa "se pidió abrir".
- Falsos positivos de la lista de bloqueo en el lanzador (D015): decidir si se compara por palabra completa.
- El ejemplo del paso, `do launch "spotify"`, no se puede probar en este PC: Spotify no está en el menú Inicio.
- `PROMPT_CHAIN.md` sigue sin existir y `CLAUDE.md` sigue sin protocolo de orquestación.

## 2026-11-13. Paso C06: exportación a Obsidian (geass.crawler.export), modo D

**Qué se hizo**
- El orquestador escribió `docs/specs/C06.md`, con el formato exacto del bloque para la base sintética de C05. Todo el código lo escribió el subagente `implementer`:
  - `export/blocks.py`: `replace_managed_block`, `ExportError` y `MalformedNoteError`.
  - `export/render.py`: `render_app_block` y `clean_inline`.
  - `export/notes.py`: `export_notes`, `note_path` y `NoteResult`.
  - `graph/build.py`: lectores públicos del grafo, `element_label` y `describe_action` (la antigua `_describe`, ahora pública).
  - CLI: `python -m geass.crawler export [--app NOMBRE] [--dry-run] [--vault RUTA] [--db RUTA]`.
  - Clave nueva `export.top_routes_per_app` en `crawler.yaml` y su esquema.
  - Tests en `tests/crawler/export/`. El constructor de la base sintética pasó a `tests/crawler/conftest.py`, para compartirlo con los tests del grafo.

**Revisión del orquestador**
- Huella de hashes: 138 archivos antes, 146 después. Cambiaron solo los archivos de la tabla de la especificación. En `crawler.yaml` y en el esquema el cambio es exactamente la clave nueva. En `graph/build.py` solo hay añadidos y el cambio de nombre de `_describe`. En `tests/crawler/graph/test_build.py` no se quitó ninguna línea. La base real no se tocó (misma fecha de modificación).
- Se leyeron completos `blocks.py` y `notes.py`, que son los que escriben en la bóveda, y el test del texto escrito a mano. `render.py`, el comando `export` y el resto de tests no se leyeron línea a línea: se comprobaron con los tests y ejecutando el comando.
- Desviaciones del subagente, aceptadas:
  - `describe_action` deja el nombre sin recortar entre comillas, para dar exactamente lo mismo que antes; la especificación pedía a la vez "mismo resultado" y recortar, y chocaban si el nombre tiene espacios en los extremos;
  - `replace_managed_block` rechaza marcadores con espacios en los extremos o saltos de línea, porque romperían la idempotencia;
  - el comando lee la ruta de la bóveda antes de abrir la base, así que con la bóveda en `TODO` la base real ni se abre;
  - una nota que es un enlace simbólico se trata como error y no se toca; un fallo al generar el contenido de una app no detiene las demás.
- No hubo ronda de correcciones.

**Verificaciones**
- `.venv\Scripts\python -m pytest -m "not hardware and not windows_ui and not slow"`: 450 pasan (392 anteriores más 58 nuevos), 7 excluidos por marker.
- `.venv\Scripts\python -m ruff check .` y `ruff format --check .`: sin errores, 113 archivos.
- Texto a mano: el test `test_hand_written_text_survives_two_exports` escribe texto antes y después del bloque, cambia la base y exporta dos veces; el texto de fuera queda idéntico las dos veces y los estados son `updated` y `unchanged`.
- `python -m geass.crawler export --dry-run` con la configuración real sale con código 1 y nombra `export.obsidian_vault_path`. No crea nada.
- Base real exportada a una bóveda temporal fuera del repo: `--dry-run` no crea ningún archivo, la primera exportación crea `GEASS/Apps/darksoulsii.md` y la segunda dice "sin cambios".
- En `export/` el único borrado es el del archivo temporal propio cuando falla una escritura.
- No se corrieron los tests `windows_ui`.

**Decisiones**
- D014 en `DECISIONES.md`.

**Pendientes**
- Ruta de la bóveda, resuelto el 2026-11-14: Manuel dio `C:\Users\manue\OneDrive\Documentos\memoria crawler` y quedó en `export.obsidian_vault_path`.
  - La carpeta existe, tiene 1 elemento y no contiene `.obsidian`: Obsidian todavía no la ha abierto como bóveda.
  - Está dentro de OneDrive, así que las notas se sincronizan con la nube.
  - `export --dry-run` con la configuración real: "se crearía" `GEASS\Apps\darksoulsii.md`, código 0, sin crear nada.
  - `tests/core/test_config.py` tenía un caso que exigía que esa clave siguiera como `TODO`; se quitó ese caso. El test de C06 que comprueba la bóveda sin configurar ahora se omite, como estaba previsto. Suite: 448 pasan y 1 omitido.
  - La tabla de datos pendientes de `INSTRUCCIONES.md` (sección 10) todavía lista la ruta de la bóveda; no se editó.
  - Exportación real el 2026-11-14, con el visto bueno de Manuel tras ver la simulación: se creó `GEASS\Apps\darksoulsii.md` y una segunda exportación dio "sin cambios".
  - Problema detectado después: la bóveda de Obsidian no es la carpeta que dio Manuel, sino su subcarpeta `Crawler-Geass`, que es la que contiene `.obsidian`. La nota quedó en la carpeta de arriba, fuera de la bóveda, y Obsidian no la ve. No se movió ni se borró nada.
  - Corregido el mismo día por decisión de Manuel: `export.obsidian_vault_path` pasó a `C:\Users\manue\OneDrive\Documentos\memoria crawler\Crawler-Geass`. La exportación creó `Crawler-Geass\GEASS\Apps\darksoulsii.md` y una segunda dio "sin cambios". `Bienvenido.md` y `.obsidian` no se tocaron. Suite: 448 pasan y 1 omitido.
  - La copia suelta de la primera exportación (`memoria crawler\GEASS\Apps\darksoulsii.md`, fuera de la bóveda) se borró a mano por orden expresa de Manuel, después de comprobar que la carpeta solo contenía esa nota generada. El exportador sigue sin borrar notas.
  - Lección para el protocolo: antes de aceptar una ruta de bóveda, comprobar que contiene `.obsidian` y, si no, mirar las subcarpetas.
- Los nombres de elementos se copian a la bóveda: si se sincroniza con la nube, salen del PC. Decisión de Manuel.
- Las secciones de acciones y rutas solo están probadas con la base sintética: la base real no tiene transiciones (pendiente de C04).
- Preguntas del subagente, sin decidir: deduplicar los elementos sin nombre por su etiqueta; nombres de archivo reservados de Windows y colisiones entre ejecutables; título vacío si la app no tiene nombre visible; coste de calcular una ruta por elemento en apps grandes.
- `PROMPT_CHAIN.md` sigue sin existir y `CLAUDE.md` sigue sin protocolo de orquestación.

## 2026-11-12. Paso C05: grafo y consultas (geass.crawler.graph), modo D

**Qué se hizo**
- El orquestador escribió `docs/specs/C05.md` con todas las decisiones de diseño y una base sintética definida fila a fila. Todo el código lo escribió el subagente `implementer`:
  - `graph/text.py`: `normalize_text`.
  - `graph/model.py`: errores (`GraphError` y cinco derivados) y los modelos `ElementMatch`, `RouteStep`, `Route` y `AppCoverage`.
  - `graph/build.py`: `CrawlerGraph` (`from_store`, `resolve_app`, `find_element`, `path_to`, `coverage`) e `is_executable`.
  - CLI: `python -m geass.crawler query TEXTO --app NOMBRE [--from ID] [--limit N] [--db RUTA]` y `python -m geass.crawler coverage [--app NOMBRE] [--db RUTA]`.
  - Clave nueva `graph.coordinate_click_cost` en `crawler.yaml` y su esquema.
  - Tests en `tests/crawler/graph/`, con la base sintética construida en `tmp_path`.
- networkx 3.7 y rapidfuzz 3.14.6 instalados en `.venv`: estaban declarados pero no instalados.

**Revisión del orquestador**
- Huella de hashes: 127 archivos antes, 137 después. Cambiaron solo los archivos de la tabla de la especificación. En `crawler.yaml` y en el esquema el cambio es exactamente la clave nueva. La base real de `data/crawler/` no se tocó durante la delegación (misma fecha de modificación).
- Se leyeron completos `build.py` y `model.py`: cumplen las interfaces. De los tests se revisó la lista de casos y que todas las bases vayan a `tmp_path`; no se leyeron línea a línea. El código de los comandos `query` y `coverage` no se leyó: se comprobó ejecutándolos.
- Desviaciones del subagente, aceptadas:
  - `query` trata `ValueError` (texto vacío o `--limit 0`) como error de usuario, con salida 1 en vez de una traza;
  - "La base todavía no existe" va por stderr;
  - una transición cuyos estados no están entre los leídos se omite con un aviso en el log.
- Un error de la especificación, detectado por el orquestador antes de delegar: el caso 27 decía 10 elementos accionables y son 9.
- No hubo ronda de correcciones.

**Verificaciones**
- `.venv\Scripts\python -m pytest -m "not hardware and not windows_ui and not slow"`: 392 pasan (347 anteriores más 45 nuevos), 7 excluidos por marker.
- `.venv\Scripts\python -m ruff check .` y `ruff format --check .`: sin errores, 104 archivos.
- `python -m geass.crawler --help` lista `dump`, `compare`, `query`, `coverage` y `passive`.
- Ningún archivo de `graph/` importa pywinauto, pynput, win32gui ni comtypes, ni ejecuta SQL.
- Contra la base real: `coverage` muestra la única app que hay (1 estado, 0 transiciones); `query` sin coincidencias y con una app inexistente salen con código 1 y su mensaje.
- No se corrieron los tests `windows_ui`.

**Decisiones**
- D013 en `DECISIONES.md`.

**Pendientes**
- Las rutas solo están probadas con la base sintética: la base real tiene 1 estado y ninguna transición. Depende de la medición con uso normal que quedó pendiente en C04.
- Decidir si `path_to` debe limitarse a los estados de la app pedida (D013, limitaciones).
- `query` y `coverage` abren la base con `CrawlerStore` (migraciones y modo WAL). Si se quiere una apertura de solo lectura estricta, hay que añadirla al almacenamiento.
- Con tres o más apps que coinciden de forma exacta, el error de ambigüedad cita solo dos.
- `PROMPT_CHAIN.md` sigue sin existir y `CLAUDE.md` sigue sin protocolo de orquestación.

## 2026-11-12. Paso C04: crawler pasivo (geass.crawler.passive), modo M

**Qué se hizo**
- Núcleo, programado por el orquestador:
  - `passive/events.py`: tipos que comparten los hilos y los protocolos `Sensor` y `PrivacyPolicy`.
  - `passive/control.py`: `PauseState` y los contadores `PassiveStats`.
  - `passive/stability.py`: `StabilityTracker`, la detección de estabilidad.
  - `passive/pointer.py`: escucha del mouse con pynput, hilo de captura del elemento bajo el cursor, `match_element` e `infer_action`.
  - `passive/observer.py`: `PassiveObserver`, el bucle de sondeo, la espera de estabilidad y el registro de estados y transiciones.
  - `passive/sensor_win.py`: lecturas reales con Win32 y UI Automation.
  - `uia/backend_win.py`: nuevo método `node_at` (elemento bajo un punto).
  - Seis claves nuevas en la sección `passive` de `crawler.yaml` y su esquema.
- Parte delegada al subagente `implementer` con `docs/specs/C04.md`:
  - `passive/privacy.py`: `ConfigPrivacyPolicy`, `ShortcutTracker`, `ShortcutListener` y `key_name`.
  - `passive/hotkey.py`: atajo de pausa con `RegisterHotKey`.
  - `passive/cpu.py`: medición de CPU.
  - `passive/runner.py`: arranque, parada entre procesos y archivo de estado.
  - CLI: `python -m geass.crawler passive start [--duration S] | stop | stats`.
  - Tests de todo el paquete, incluido el núcleo, en `tests/crawler/passive/`.
- pynput 1.8.2 instalado en `.venv`: estaba declarado en `pyproject.toml` pero no instalado.

**Revisión del orquestador**
- Huella de hashes antes de delegar: 112 archivos. Después: 126. Cambiaron solo los archivos de la tabla de la especificación; los seis archivos del núcleo quedaron idénticos y no se creó nada en `data/`.
- Se leyó completo el código nuevo de `src/` (`privacy.py`, `hotkey.py`, `cpu.py`, `runner.py` y el cambio de `__main__.py`). Cumple las interfaces y las convenciones. De los tests se revisó la lista de casos y que ninguno use el sensor real ni arranque escuchas; no se leyeron línea a línea.
- Desviaciones del subagente, todas aceptadas:
  - el caso 54 no se podía cumplir literal, porque un docstring del núcleo contenía la palabra `Controller`. Lo resolvió analizando el código con `ast`. El orquestador quitó después la palabra del docstring;
  - el atajo de pausa arranca antes que el observador y el capturador, para que un fallo no deje nada en marcha;
  - el estado se escribe también al empezar, y las notas de sesión llevan una clave `error` si el arranque falla.
- Dos fallos eran de la especificación, no del subagente: el docstring anterior, y que `ruff format --check .` revisa los bloques de Python de los Markdown y fallaba en `docs/specs/C04.md`. Se corrigieron y quedaron anotados en "Cambios" de la especificación.
- No hubo segunda ronda de correcciones al subagente.

**Verificaciones**
- `.venv\Scripts\python -m pytest -m "not hardware and not windows_ui and not slow"`: 347 pasan (236 anteriores más 111 nuevos), 7 excluidos por marker.
- `.venv\Scripts\python -m ruff check .` y `ruff format --check .`: sin errores, 93 archivos.
- `passive --help` lista `start`, `stop` y `stats`; `passive stop` sin proceso en marcha sale con código 1.
- Prueba del núcleo con dobles antes de delegar: estados, transición con elemento, clics interrumpidos, pausa, app excluida, volcado parcial y tiempo límite.
- Sensor real, solo lectura, en dos hilos a la vez: funcionan la ventana en primer plano, el elemento bajo el cursor y el volcado.
- No se corrieron los tests `windows_ui`, y este paso no añade ninguno.

**Medición de CPU**
- `passive start --duration 600` en el PC de desarrollo, el 2026-11-12: 600,0 s, 0,26 % de un núcleo (0,03 % de la máquina). Presupuesto: 5 % de un núcleo.
- La medición no es representativa de un uso normal:
  - durante los 10 minutos la ventana en primer plano fue siempre la misma, un juego a pantalla completa cuyo árbol tiene 1 nodo;
  - hubo 268 volcados, 1 estado registrado y 0 transiciones;
  - se vieron 0 clics.
- Segunda medición el mismo día, pedida por Manuel para repetirla con uso normal: 600,0 s, 0,27 % de un núcleo (0,03 % de la máquina), 272 volcados y 48,8 s esperando volcados. Salió igual que la primera: una sola app en primer plano (el mismo estado ya conocido, ningún estado nuevo), 0 clics vistos y 0 transiciones. Tampoco es representativa.
- Lo que sí muestran las dos: con una sola ventana y entrada constante, el sondeo y la resincronización cada 2 s cuestan cerca del 0,3 % de un núcleo.
- En la primera medición el proceso pasó 52,3 s de los 600 esperando volcados (unos 195 ms cada uno, con 1 nodo). Ese tiempo casi no es CPU propia: lo gasta la app que responde a UI Automation, y esta medición no lo cuenta.

**Decisiones**
- D012 en `DECISIONES.md`: hilos, estabilidad, atribución de transiciones, atajo de pausa sin gancho de teclado, medición de CPU y limitaciones conocidas.

**Pendientes**
- Repetir la medición de 10 minutos con uso normal del escritorio (varias apps no excluidas y clics). Las dos de hoy no sirven para dar por cumplido el presupuesto.
- El camino real del clic (pynput, hilo de captura, transición) no se ha visto funcionar de punta a punta: en 20 minutos de medición no se vio ningún clic. Manuel confirmó que siguió jugando durante la segunda medición, sin usar otras apps, así que los 0 clics no indican por sí solos un fallo. Tampoco prueban que la escucha funcione: no se sabe si en el juego hubo clics de mouse. Está probado con dobles y por partes. Se confirma en la próxima medición mirando `presses_seen` y `transitions_recorded` en `passive stats`.
- `max_capture_delay_s` (0,15 s) puede ser corto: sobre la ventana del juego, leer el elemento bajo el cursor tardó entre 147 y 203 ms. Falta medirlo en apps normales (contador `press_elements_late`).
- Preguntas del subagente, sin decidir:
  - `passive start` no configura el logging, así que los errores de los hilos no se guardan en `data/logs`;
  - el atajo de pausa no da ningún aviso al pausar ni al reanudar;
  - si el proceso muere de golpe, el archivo de estado queda con `running: true`.
- Sin tests `windows_ui` para `WinSensor` ni para el crawler pasivo completo.
- El texto de los atajos no se guarda en la base (D012). Solo importa si se activa `capture_shortcuts`.
- Eventos de UI Automation: queda la entrada (`ChangeHint`), sin implementar. `use_uia_events: true` solo deja un aviso en el log.
- La base real `data/crawler/geass_crawler.sqlite` existe desde hoy, con las sesiones 1 y 2 de las mediciones.
- `CLAUDE.md` sigue sin protocolo de orquestación ni definición de los modos D y M. Se siguió `docs/specs/README.md` y el reparto que dio Manuel.
- `PROMPT_CHAIN.md` sigue sin existir: no se pudo marcar la casilla.

## 2026-11-11. Paso O00: prueba de humo de la orquestación

**Qué se hizo**
- `implementer.md` estaba en la raíz del repo, no en `.claude/agents/`. Se movió a `.claude/agents/implementer.md` sin cambiar su contenido. Su campo `model` es `opus`.
- `docs/specs/README.md`: cómo se escriben y nombran las especificaciones, y el ciclo de una tarea delegada.
- `docs/specs/_plantilla.md`: objetivo, archivos, interfaces, casos de prueba, criterios de aceptación, reglas de seguridad aplicables y qué no hacer.
- `docs/specs/O00.md`: especificación de `geass.core.version()` con su test.
- Primer intento de delegación (sesión anterior): falló con "Agent type 'implementer' not found". La sesión carga los subagentes al arrancar, y en ese momento el archivo no estaba en `.claude/agents/`. No se delegó a otro tipo de agente, porque eso no probaría ni el subagente ni su modelo.
- Segundo intento (sesión nueva): el tipo de agente `implementer` ya existía y la delegación funcionó a la primera. El subagente recibió la ruta de la especificación y las condiciones del entorno (usar `.venv\Scripts\python`, no instalar nada, no correr `windows_ui`, repo sin git).
- Lo que hizo el subagente:
  - creó `src/geass/core/package_info.py` con `PACKAGE_NAME = "geass"` y `version() -> str`, que devuelve `importlib.metadata.version(PACKAGE_NAME)` sin capturar `PackageNotFoundError`;
  - modificó `src/geass/core/__init__.py`: una línea de importación y `"version"` al final de `__all__`;
  - creó `tests/core/test_package_info.py` con los 4 casos de la especificación.

**Revisión del orquestador**
- Como el repo no está en git, antes de delegar se guardó fuera del repo el hash SHA256 de cada archivo (103 archivos, sin `.venv`, cachés, `data/` ni `models/`) y una copia de `src/geass/core/__init__.py`. Después de la delegación: 105 archivos. Cambiaron exactamente los tres de la especificación (dos nuevos y uno modificado) y ningún otro.
- El cambio de `__init__.py` son solo dos líneas añadidas: la importación y la entrada de `__all__`, en orden alfabético.
- Se leyó el código completo de los dos archivos nuevos. La interfaz coincide con la especificación (nombre, firma, constante, sin dependencias nuevas). Docstrings y comentarios en español, nombres en inglés, tipado en la función pública y en los tests, sin emojis ni guiones largos.
- Nada fuera de alcance: no se tocaron `pyproject.toml`, `geass.__version__`, la CLI, la bitácora ni las especificaciones.
- No hizo falta pedir ninguna corrección.

**Verificaciones**
- Corridas por el orquestador después de la revisión; coinciden con las que reportó el subagente:
  - `.venv\Scripts\python -m pytest -m "not hardware and not windows_ui and not slow"`: 236 pasan (los 232 anteriores más 4 nuevos), 7 excluidos por marker.
  - `.venv\Scripts\python -m ruff check .`: sin errores.
  - `.venv\Scripts\python -m ruff format --check .`: 72 archivos ya formateados.
  - `.venv\Scripts\python -c "from geass.core import version; print(version())"`: imprime `0.1.0`.
- No se corrieron los tests `windows_ui`.

**Desviaciones**
- Una, reportada por el subagente y aceptada: en el caso de prueba 2 se añadió `monkeypatch.delenv(paths.ROOT_ENV, raising=False)` antes de llamar a `project_root()`, para que el test lea el `pyproject.toml` de este repo aunque `GEASS_ROOT` esté definida. La especificación no lo pedía; no cambia ninguna interfaz.

**Cómo fue la prueba**
- Funcionó: la especificación bastó por sí sola (el subagente no hizo preguntas ni decidió interfaces), respetó el alcance de tres archivos, no editó bitácora ni especificaciones, y su reporte siguió el formato de `implementer.md` y resultó fiel a lo que encontró la revisión.
- No funcionó o costó:
  - un subagente añadido a `.claude/agents/` con la sesión abierta no se carga; hizo falta una sesión nueva;
  - sin git no hay diff: la revisión dependió de una huella de hashes hecha a mano antes de delegar. Si se olvida ese paso, no hay forma fiable de saber qué tocó el subagente;
  - la herramienta de delegación no le dice al orquestador qué modelo corrió el subagente; hubo que buscarlo después en el registro de la ejecución.
- Riesgo que señaló el subagente: `version()` lee los metadatos del paquete instalado. Si se cambia la versión en `pyproject.toml` sin reinstalar, los casos 2 y 3 fallan hasta reinstalar.

**Decisiones**
- Ninguna nueva.

**Pendientes**
- Modelo del subagente, verificado el 2026-11-12: el registro de la ejecución (`subagents/agent-<id>.jsonl`, en la carpeta de la sesión dentro de `~/.claude/projects/`) marca `claude-opus-5-5` en sus 26 mensajes y ningún otro modelo. Coincide con `model: opus`. Manuel dijo primero que fue Opus y luego que no lo recordaba, así que el dato válido es el del registro, no la confirmación con `/tasks`.
- Las casillas de "Criterios de aceptación" de `docs/specs/O00.md` no se marcaron: el README dice que una especificación no se reescribe después de implementada. Falta decidir si marcar casillas cuenta como reescribir.
- Poner el repo en git (o mantener la huella de hashes como paso obligatorio del ciclo) para que la revisión tenga un diff real.
- `CLAUDE.md` no contiene ningún protocolo de orquestación: no menciona subagentes, especificaciones ni los modos D y M que cita `implementer.md`. El README de `docs/specs/` describe el ciclo tal como se deduce del encargo y de `implementer.md`; hay que contrastarlo con el protocolo cuando esté en `CLAUDE.md`.
- `PROMPT_CHAIN.md` sigue sin existir.

## 2026-11-11. Paso C03: almacenamiento (geass.crawler.store)

**Qué se hizo**
- `store/migrations.py`: esquema de la sección 6.4, tabla de versión y lista de migraciones.
- `store/db.py`: `CrawlerStore` con funciones de repositorio para apps, estados, elementos, transiciones (conteo y latencia media) y sesiones.
- `store/records.py`: registros Pydantic.
- `store/recording.py`: `record_dump`, que guarda la app, el estado y los elementos de un volcado.
- `store/screens.py`: captura con mss, tapado de campos de texto, deduplicación por hash perceptual y retención.
- Nueva clave `store.redact_control_types` en `crawler.yaml`.

**Verificaciones**
- `pytest -m "not windows_ui"`: 232 tests pasan. Todos los de la base usan un archivo temporal.
- `pytest -m windows_ui`: 7 tests, incluida una captura real con mss que no se guarda en disco. Se ejecutó la suite 5 veces: 4 pasaron completas y en 1 falló el test de repetibilidad de la firma con la Calculadora (del paso C02). No se reprodujo ni se capturó la causa. El test ahora imprime qué elementos cambiaron si vuelve a fallar.
- `ruff check .` y `ruff format --check .`: sin errores.
- Un test encontró un error real, ya corregido: dos capturas con el mismo hash y distinta resolución chocaban en el nombre de archivo.

**Decisiones**
- D011 en `DECISIONES.md`, con las limitaciones conocidas.

**Pendientes**
- Nada usa todavía la base real de `data/crawler/`: no se creó ningún archivo ahí.
- Fallo intermitente sin explicar en el test de repetibilidad de la firma con la Calculadora (1 de 5 ejecuciones). Sospecha sin confirmar: la app todavía estaba terminando de cargar en un arranque en frío.
- La captura de ventanas completa (`capture_window` con una ventana real) solo se probó con una función de captura falsa.
- El nombre de las pestañas se guarda tal cual. Manuel decide si `Tab` pasa a ser contenedor volátil.
- `PROMPT_CHAIN.md` sigue sin existir: no se pudo leer el paso ni marcar su casilla.

## 2026-11-11. Paso C02: firmas de estado (geass.crawler.signature)

**Qué se hizo**
- `signature/rules.py`: reglas de normalización compiladas desde `crawler.yaml`.
- `signature/compute.py`: firma estructural y firma detallada.
- `signature/compare.py`: comparación de dos volcados y explicación de qué diferencias afectan a cada firma y cuáles se ignoran.
- CLI: `python -m geass.crawler compare a.json b.json [--max-lines N]`.
- Sección `signature` de `crawler.yaml` reescrita con todas las reglas, y su esquema.
- `scripts/capture_uia_fixtures.py` y seis volcados reales en `tests/fixtures/uia/`, sin datos personales.
- `tests/crawler/conftest.py`: fixtures compartidas para abrir y cerrar apps reales. Ahora ignoran las ventanas ocultas de apps de la tienda suspendidas.

**Verificaciones**
- `pytest -m "not windows_ui"`: 182 tests pasan.
- `pytest -m windows_ui -s`: 6 tests pasan.
  - Calculadora: 5 de 5 volcados con la misma firma estructural.
  - Configuración: las páginas Mouse y Colores dan firmas distintas, y volver a Mouse da la primera firma. Las firmas en vivo coinciden con las de los volcados guardados.
- Con los volcados guardados: la Calculadora volcada dos veces da la misma firma; el Bloc de notas con dos archivos distintos da la misma firma estructural y detallada; las dos páginas de Configuración dan firmas distintas.
- `ruff check .` y `ruff format --check .`: sin errores.

**Decisiones**
- D010 en `DECISIONES.md`, con las limitaciones conocidas.

**Pendientes**
- El caso del Bloc de notas con distinto texto solo se prueba con volcados guardados, no en vivo.
- La captura de fixtures dejó dos pestañas (`nota.txt` e `informe 2026-11-11 v2.txt`) en la sesión del Bloc de notas de Manuel, porque Windows 11 recuerda las pestañas. Los archivos eran temporales y ya no existen. Hay que cerrarlas a mano.
- La meta de repetibilidad del 95 % solo se midió con la Calculadora.
- `PROMPT_CHAIN.md` sigue sin existir: no se pudo leer el paso ni marcar su casilla.

## 2026-11-11. Paso C01: lector UIA (geass.crawler.uia)

**Qué se hizo**
- `uia/schema.py`: `Rect`, `Element` y `WindowDump`.
- `uia/reader.py`: recorrido del árbol con límites de profundidad, nodos y tiempo, y marca de volcado parcial.
- `uia/backend_win.py`: lectura con pywinauto (backend uia) y caché de propiedades. Solo lee.
- `uia/dump.py`: `dump_window(handle o ventana activa)`, con los límites de `crawler.yaml` y la lista de apps excluidas.
- CLI: `python -m geass.crawler dump --active | --handle N [--out archivo.json] [--delay s]`.
- Nueva clave `uia.tree_view` en `crawler.yaml`.
- Tests rápidos con nodos falsos y tests `windows_ui` con el Bloc de notas y la Calculadora.

**Verificaciones**
- `pytest -m "not windows_ui"`: 119 tests pasan.
- `pytest -m windows_ui -s`: 3 tests pasan. Ambas apps se abrieron, se volcaron y se cerraron.
- Tiempo de volcado medido (meta: menos de 1 s):
  - Bloc de notas: 53 nodos, profundidad 6, 123 ms.
  - Calculadora: 54 nodos, profundidad 5, 129 ms.
- `ruff check .` y `ruff format --check .`: sin errores.
- Se instalaron en `.venv` pywinauto 0.6.9, comtypes 1.5.0 y pywin32. No se instaló el resto del grupo `crawler`.

**Decisiones**
- D008 y D009 en `DECISIONES.md`.

**Pendientes**
- Resuelto el 2026-11-11: Manuel aprobó excluir las categorías habituales (gestores de contraseñas, credenciales de Windows, correo y mensajería, y navegadores). La lista de ejecutables de `apps.excluded` la armó Claude y Manuel debe revisarla. La CLI `dump` ya funciona: 123 tests pasan y un volcado real de la ventana activa dio 705 nodos en 410 ms.
- `PROMPT_CHAIN.md` sigue sin existir: no se pudo leer el paso ni marcar su casilla.
- El nombre de los elementos y el título de la ventana sí se registran. En navegadores y editores eso puede incluir texto de la página o el nombre del archivo abierto. Hay que resolverlo en las firmas (C02) y antes de guardar en SQLite.
- El límite de tiempo se comprueba entre llamadas COM: una llamada colgada no se interrumpe.
- No se midieron apps grandes (navegador, Office, Explorador).

## 2026-11-11. Paso P01: núcleo compartido (geass.core)

**Qué se hizo**
- `core/schema.py`: base de los modelos y manejo de los valores `TODO`.
- `core/schemas/`: modelos Pydantic de `arm.yaml`, `board.yaml`, `safety.yaml` y `crawler.yaml`.
- `core/config.py`: cargador con validación y perfil del brazo (`real` o `test`) por argumento o por `GEASS_ARM_PROFILE`.
- `core/errors.py`: `ConfigError`, `ConfigValidationError` y `ConfigIncompleteError`.
- `core/log.py`: consola con rich y archivo rotativo en `data/logs/`.
- `core/units.py`: grados y radianes, milímetros y metros.
- `core/paths.py`: raíz, `config`, `data`, `models` y `data/logs`.
- Tests en `tests/core/`: carga, `TODO`, perfiles, validación, unidades, rutas y logging.

**Verificaciones**
- `pytest`: 89 tests pasan.
- `ruff check .` y `ruff format --check .`: sin errores.

**Decisiones**
- D006 y D007 en `DECISIONES.md`.

**Pendientes**
- `PROMPT_CHAIN.md` sigue sin existir: no se pudo leer el paso ni marcar su casilla.
- Los modelos fijan la forma de dos datos que la especificación no detalla: `authorization.allowed_windows` como lista de `{executable, title_pattern}` y `aruco.corner_offset_m` como un solo número. Revisar cuando Manuel dé esos datos.
- Siguen pendientes todos los datos de la sección 10 de `INSTRUCCIONES.md`.

## 2026-11-10. Paso P00: esqueleto del monorepo

**Qué se hizo**
- `pyproject.toml` con layout `src`, paquete `geass`, grupos opcionales `arm`, `crawler` y `dev`, y configuración de ruff y pytest (markers `hardware`, `windows_ui` y `slow`).
- Carpetas de la sección 7 de `INSTRUCCIONES.md`, con `__init__.py` en cada paquete y `.gitkeep` en las carpetas vacías.
- `python -m geass.arm` y `python -m geass.crawler`: CLI typer que solo muestra la ayuda.
- `.gitignore`, `README.md` y plantillas de `docs/`.
- `config/arm.yaml`, `board.yaml`, `safety.yaml` y `crawler.yaml` con todas las claves, y los datos de la sección 10 como `TODO`. `config/arm.test.yaml` con valores ficticios.
- `tests/test_skeleton.py`: comprueba que los paquetes importan, que las CLI muestran ayuda y que los YAML se leen.
- Sin lógica implementada.

**Verificaciones**
- `pip install -e ".[dev]"` en `.venv` (Python 3.12): correcto.
- `pytest`: 29 tests pasan.
- `ruff check .` y `ruff format --check .`: sin errores.
- `python -m geass.arm --help` y `python -m geass.crawler`: muestran la ayuda y salen con código 0.
- Revisión de los YAML contra `INSTRUCCIONES.md`. Se corrigió: `yes` sin comillas en una lista de patrones, un `TODO` sin comentario, la semilla de percepción, una clave que permitía desactivar la regla de campos de contraseña y los límites ficticios de `arm.test.yaml`. La revisión de documentación y la de `arm.yaml` quedaron incompletas.
- No se instalaron todavía los grupos `arm` y `crawler` (torch, mujoco y demás son pesados y este paso no los usa).

**Decisiones**
- D001 a D005 en `DECISIONES.md`.

**Pendientes**
- `PROMPT_CHAIN.md` no existe en el repo: no se pudo leer el paso ni marcar su casilla.
- El PC solo tiene Python 3.12; la especificación pide 3.11.
- El repo todavía no está inicializado en git.
- `store.capture_screens` queda en `false`: una captura guarda como imagen el texto de los campos. Manuel decide si se activa.
- Todos los datos de la sección 10 de `INSTRUCCIONES.md`.
- Revisar con Manuel los valores marcados "provisional" en `config/`, en especial los de `safety.yaml`.
