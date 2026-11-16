# Decisiones

Decisiones de arquitectura y cambios de dependencias. Cada una lleva motivo y alternativas descartadas.

## Plantilla

```
## D<NNN>. <título> (AAAA-MM-DD)

**Decisión:** ...
**Motivo:** ...
**Alternativas descartadas:** ...
```

## D001. Dependencias iniciales (2026-11-10)

**Decisión:** las dependencias son las de la sección 8 de `INSTRUCCIONES.md`, sin añadidos.
- Base: numpy, scipy, pydantic, pyyaml, typer, rich, pandas y pyarrow.
- `arm`: torch, mujoco, opencv-contrib-python, chess y pyserial.
- `crawler`: pywinauto, comtypes, pywin32, pynput, mss, pillow, imagehash, networkx, rapidfuzz y torchvision.
- `dev`: pytest y ruff.

Solo se fijan versiones mínimas. `python-chess` se declara como `chess`, que es el nombre actual del paquete en PyPI.

**Motivo:** seguir la especificación. Las versiones mínimas evitan versiones muy antiguas sin atar el proyecto a una exacta.

**Alternativas descartadas:** versiones exactas (se decidirá un archivo de bloqueo si hace falta reproducibilidad estricta).

## D002. Backend de empaquetado: setuptools (2026-11-10)

**Decisión:** `setuptools` como backend de build, con layout `src`.

**Motivo:** viene con pip, no añade herramientas y soporta la instalación editable.

**Alternativas descartadas:** hatchling y poetry (una herramienta más sin beneficio para un proyecto personal).

## D003. Marcadores de plataforma en el grupo crawler (2026-11-10)

**Decisión:** pywinauto, comtypes y pywin32 llevan el marcador `sys_platform == 'win32'`.

**Motivo:** `pip install -e ".[arm,crawler,dev]"` no debe fallar en Linux (Colab), aunque el crawler solo funcione en Windows.

**Alternativas descartadas:** dejar el grupo sin marcadores (la instalación falla fuera de Windows).

## D004. PyTorch en CPU (2026-11-10)

**Decisión:** se declara `torch` sin índice especial. En Windows, el paquete por defecto de PyPI es la variante de CPU. En Colab se usa el torch que ya trae el entorno.

**Motivo:** la RX 570 no se usa para entrenar. No se intenta ROCm ni DirectML.

**Alternativas descartadas:** fijar el índice de CPU de PyTorch en la configuración (pip no lo permite desde `pyproject.toml`).

## D005. Convenciones de los YAML de configuración (2026-11-10)

**Decisión:**
- Los YAML usan unidades SI (metros, radianes y segundos) con la unidad como sufijo de la clave (`_m`, `_rad`, `_s`). Excepciones con sufijo propio: `_ms` en el protocolo con el ESP32 (`watchdog_ms`), `_us` para anchos de pulso de servo, `_hz`, `_px` y, en la retención del crawler, `_days` y `_mb`.
- Un dato pendiente se escribe como la cadena `TODO`, con un comentario que dice qué es y en qué unidad.
- Los ajustes de software que la especificación no fija llevan el comentario "provisional".
- Cada YAML tiene `schema_version`.
- Las reglas no negociables de privacidad del crawler no tienen clave de configuración: no se pueden desactivar desde un YAML.
- Los tests usan `--import-mode=importlib`, sin `__init__.py` dentro de `tests/`, para que carpetas como `tests/arm/chess` no oculten paquetes instalados.

**Motivo:** una sola convención de unidades evita conversiones ocultas; el sufijo hace visible la unidad. `TODO` como cadena permite que el cargador de configuración lo detecte y falle con un error claro.

**Alternativas descartadas:** grados y milímetros en los YAML (más legibles, pero obligan a convertir al cargar y contradicen la regla de unidades internas); `null` para los pendientes (no distingue "pendiente" de "no aplica").

## D006. Valores TODO en los esquemas de configuración (2026-11-11)

**Decisión:**
- Un campo que puede estar pendiente se declara como `Todo[tipo]` con `todo("qué es y en qué unidad")`. Al validar, la cadena `TODO` se convierte en una marca interna; al leer ese atributo, el modelo lanza `ConfigIncompleteError` con la ruta de la clave, su significado y el archivo.
- El significado vive en la descripción del campo del modelo, no en el comentario del YAML. Un test exige que todo campo `Todo` tenga descripción.
- Solo los campos declarados con `Todo` admiten `TODO`. En cualquier otro campo es un error de validación.
- Para consultar sin lanzar error existen `is_pending`, `known` y `pending_keys`.
- Los modelos son inmutables y rechazan claves desconocidas.
- Las reglas no negociables de `safety.yaml` (`require_sandbox`, `never_type_text`, `require_explicit_order`, `stop_if_window_unconfirmed`) se tipan como `Literal[True]`: el YAML no puede desactivarlas. El selector de soluciones exige que la IK analítica sea el último recurso.

**Motivo:** el error aparece justo donde se usa el dato que falta, con un mensaje que dice qué pedirle a Manuel, y el resto de la configuración sigue siendo utilizable.

**Alternativas descartadas:** fallar al cargar si hay algún `TODO` (bloquea todo el proyecto hasta tener el hardware medido); campos opcionales con `None` (el error llegaría tarde y sin contexto, como un `TypeError`); leer el significado de los comentarios del YAML (frágil).

## D007. Perfil del brazo y logging (2026-11-11)

**Decisión:**
- El perfil del brazo se elige con el argumento `profile` o con la variable de entorno `GEASS_ARM_PROFILE`. Valores: `real` (`arm.yaml`, por defecto) y `test` (`arm.test.yaml`). El argumento tiene prioridad.
- El cargador rechaza un archivo marcado `fictitious: true` como perfil real, y exige esa marca en el perfil de test.
- La raíz del proyecto se detecta buscando `pyproject.toml`; se puede forzar con `GEASS_ROOT`.
- Logging en el logger `geass`: consola con rich y archivo rotativo `data/logs/geass.log` (5 MB, 5 respaldos). El nivel sale del argumento, de `GEASS_LOG_LEVEL` o es `INFO`. Estos valores son parámetros de `setup_logging` con valores por defecto en `geass.core.log`, no claves de un YAML.
- El módulo de logging se llama `geass.core.log`, para no confundirlo con `logging` de la biblioteca estándar.

**Motivo:** el perfil por variable de entorno permite correr la CLI y los tests sin tocar código. La marca `fictitious` evita mover el brazo real con datos inventados. El logging se configura antes de leer cualquier YAML, y la sección 7 de `INSTRUCCIONES.md` solo define cuatro archivos de configuración.

**Alternativas descartadas:** un quinto YAML para el logging; elegir el perfil por una ruta de archivo arbitraria (permitiría cargar cualquier archivo como si fuera el brazo real).

## D008. Lector UIA: lectura con caché y recorrido separado del backend (2026-11-11)

**Decisión:**
- El lector usa la capa UIA de pywinauto (`pywinauto.uia_defines.IUIA`) con una petición de caché: una llamada COM por nodo trae sus hijos con todas las propiedades, en vez de una llamada por propiedad.
- El recorrido (`reader.walk_tree`) no depende de Windows: trabaja sobre el protocolo `RawNode`. El backend real está en `backend_win.py` y los tests rápidos usan nodos falsos.
- Recorrido en anchura. Al llegar a un límite o fallar un elemento, el volcado sigue y queda `partial=True` con el motivo: `max_depth`, `max_nodes`, `timeout` o `element_error`.
- Nueva clave `uia.tree_view` en `crawler.yaml` (`control` o `raw`). Por defecto `control`, que omite nodos de relleno.
- La ruta de un elemento es `Tipo[i]/Tipo[j]/...`, donde el índice cuenta los hermanos del mismo tipo.
- Para ventanas de apps de la tienda, el proceso que se registra es el de la app real y no `ApplicationFrameHost.exe`.

**Motivo:** las llamadas COM entre procesos dominan el tiempo; con caché el Bloc de notas y la Calculadora se vuelcan en unos 125 ms. Separar el recorrido permite probar límites y errores sin escritorio.

**Alternativas descartadas:** `UIAElementInfo` de pywinauto propiedad a propiedad (decenas de llamadas COM por nodo); pedir todo el subárbol en una sola llamada con caché (no permite aplicar los límites de nodos y tiempo durante la lectura).

## D009. Privacidad del lector UIA (2026-11-11)

**Decisión:**
- El esquema `Element` no tiene ningún campo para valores de texto y el backend nunca pide ValuePattern, TextPattern ni LegacyIAccessible. No existe una opción para leer valores, aunque el paso decía "por defecto no se lee": la regla de `CLAUDE.md` dice que el valor de los campos de texto no se registra. Si el ejecutor lo necesita más adelante, se decidirá entonces con Manuel.
- `dump_window` aplica la lista `apps.excluded` antes de leer nada de la ventana. Si no puede identificar el proceso y hay exclusiones, tampoco lee.
- Mientras `apps.excluded` siga como `TODO`, la CLI `dump` falla con `ConfigIncompleteError`. No hay opción para saltarse la lista.

**Motivo:** las reglas de privacidad del crawler no son negociables y deben cumplirse desde el primer módulo que lee la interfaz.

**Alternativas descartadas:** un parámetro `read_values` apagado por defecto; tratar la lista pendiente como vacía.

## D010. Firmas de estado (2026-11-11)

**Decisión:**
- Cada elemento que sobrevive a las reglas aporta una ficha: profundidad, tipo de control, AutomationId, nombre normalizado, habilitado y, si es un contenedor volátil, el rango de cantidad de hijos.
- Firma estructural: SHA-256 del proceso (en minúsculas), la clase de ventana y el multiconjunto de (profundidad, tipo, AutomationId o nombre normalizado si no hay AutomationId, rango). El orden de los hermanos no cuenta.
- Firma detallada: igual, pero cada ficha lleva siempre el nombre normalizado y si el elemento está habilitado.
- El nombre del elemento raíz se sustituye por la parte estable del título de la ventana.
- No entran en ninguna firma: rectángulos, fuera de pantalla, patrones, rutas, handle ni tiempos.
- Todas las reglas están en la sección `signature` de `crawler.yaml`: `ignored_control_types`, `volatile_container_types`, `prune_empty_control_types`, `count_buckets`, `variable_text_patterns`, `title_patterns` y `default_title_pattern`. Sustituyen a la clave `ignore_text_patterns`. Un test comprueba que el código no tenga reglas escritas a mano.
- Reglas añadidas a la sección 6.3 tras observar apps reales: ignorar barras de desplazamiento y descartar paneles y grupos que quedan vacíos. Sin ellas, el Bloc de notas cambia de firma cuando el texto no cabe en la ventana.
- El hash incluye una versión del algoritmo (`ALGORITHM_VERSION`), para poder invalidar firmas guardadas si cambia.

**Motivo:** la firma estructural debe sobrevivir a cambios de contenido y la detallada debe notar variantes como un botón deshabilitado. Un multiconjunto por nivel tolera reordenamientos.

**Alternativas descartadas:** hash del árbol con orden y jerarquía exactos (cualquier reordenamiento cambia la firma); usar solo AutomationId (muchas apps no lo ponen); cantidad exacta de hijos en listas (cada carpeta o documento sería una vista distinta).

**Limitaciones conocidas:**
- Una lista que cruza un límite de rango (por ejemplo de 4 a 5 pestañas) cambia la firma estructural.
- Los AutomationId no se normalizan: una app que genere identificadores variables dará firmas inestables.
- La clase de ventana tampoco se normaliza; algunas tecnologías ponen un identificador variable en ella.
- Los textos sin AutomationId cuentan para la firma estructural, así que la misma vista en otro idioma es otra firma.
- Solo se validó con tres apps. La meta del 95 % queda por medir con datos del crawler pasivo.

## D011. Almacenamiento del crawler (2026-11-11)

**Decisión:**
- SQLite con las cinco tablas de la sección 6.4 más `schema_version` y `screenshots`. Las migraciones son una lista numerada en `store/migrations.py`; cada una se aplica en una transacción y se registra en `schema_version`. Una base más nueva que el código se rechaza.
- Un estado se identifica por la app y la firma estructural. La firma detallada se guarda como la última variante vista, no como estado aparte.
- Un elemento se identifica por el estado y su ruta. Al volver a guardar un estado, los elementos conservan su id y los que ya no aparecen no se borran, para no romper las transiciones que los usan.
- Una transición se identifica por origen, destino, elemento, tipo de acción y fuente. Cada observación suma al conteo; la latencia media es incremental y solo cuenta las observaciones que traen latencia (columna `latency_samples`).
- Fechas en texto ISO 8601 en UTC. La base recibe un reloj inyectable, para los tests.
- Modo WAL y claves foráneas activadas. Las escrituras son atómicas y seguras entre hilos.
- Dependencias instaladas en `.venv` para este paso: mss, pillow e imagehash, que ya estaban declaradas en el grupo `crawler`.

**Privacidad al guardar (`record_dump`):**
- No se guarda nada de una app excluida ni de un volcado sin proceso conocido.
- Solo se guardan los elementos que las reglas de firma conservan. El contenido de listas, cuadrículas, documentos, árboles y tablas no se guarda.
- Del título de la ventana solo se guarda la parte estable.

**Capturas:**
- Siguen apagadas por defecto (`store.capture_screens: false`).
- Solo se captura la ventana en primer plano y nunca una app excluida.
- Antes de guardar se tapan en negro los campos de contraseña y los tipos de `store.redact_control_types` (nueva clave; por defecto `Edit` y `Document`).
- Deduplicación global por hash perceptual (`imagehash.phash`) con la distancia de `phash_max_distance`, solo entre capturas del mismo tamaño.
- Retención explícita con `apply_retention`: primero las vencidas por edad y luego las más antiguas hasta respetar el tamaño total. Solo borra archivos registrados en la base y dentro de la carpeta de capturas.

**Motivo:** la firma estructural es la que define la vista, así que es la clave natural del grafo. Guardar solo los elementos conservados resuelve el pendiente de privacidad de C01 para el contenido de listas y documentos.

**Alternativas descartadas:** un estado por cada firma detallada (multiplica los nodos del grafo); borrar los elementos que desaparecen (deja transiciones sin elemento); comparar hashes solo dentro del mismo estado (la misma pantalla llega por varios estados).

**Limitaciones conocidas:**
- Los nombres de los elementos conservados se guardan tal cual. Las pestañas no son un contenedor volátil, así que el nombre de una pestaña (que puede ser un nombre de archivo) sí se guarda. Para evitarlo hay que añadir `Tab` a `volatile_container_types`.
- La deduplicación recorre todos los hashes; con decenas de miles de capturas habrá que indexar.
- Una captura tapa los campos de texto, pero no el contenido de listas ni de páginas.

## D012. Crawler pasivo: hilos, estabilidad y atribución de transiciones (2026-11-12)

**Decisión:**
- Tres hilos, además del principal:
  - el de escucha del mouse (pynput) solo anota posición y hora y vuelve;
  - el de captura lee el elemento bajo el cursor en cuanto llega esa anotación;
  - el de sondeo vuelca ventanas, decide la estabilidad y es el único que escribe en la base.

  Los demás le publican eventos con `PassiveObserver.post`. Ese mismo punto de entrada es el previsto para los eventos de UI Automation (`ChangeHint`), que no se implementan en este paso.
- COM: los hilos de captura y de sondeo entran en el apartamento multihilo y comparten un único backend de UI Automation, que nunca se crea en el hilo principal.
- En reposo solo se consulta la ventana en primer plano y su título con Win32, cada `poll_interval_s`. El árbol se vuelca si cambia la ventana o el título, si llega un evento, o al resincronizar: cada `resync_interval_s` si hubo entrada de teclado o mouse desde el último volcado (`GetLastInputInfo`, que no dice qué se pulsó), y con espera que se duplica hasta `resync_max_interval_s` si no la hubo.
- Estabilidad: la huella es (handle, firma estructural, firma detallada). Estable cuando no cambia durante `stability_window_s`; si se agota `stability_timeout_s` se registra lo último que se leyó. Un volcado fallido cuenta como cambio.
- Certeza: no se registra nada, y se olvida el estado conocido, si la ventana no tiene proceso identificado, si está excluida, si cambia durante el volcado o si el volcado es parcial.
- Una transición solo se registra si el clic fue en la ventana del último estado estable y no hubo otra acción durante la espera. Dos acciones dentro de la misma espera pierden la atribución: se registra el estado final y ninguna transición.
- El elemento del clic se identifica buscando en el volcado del estado de origen lo que `ElementFromPoint` leyó (tipo, AutomationId, nombre, clase y punto dentro del rectángulo). Si no es un elemento que la base guarda, la transición va sin elemento y con acción `unknown`.
- Tipo de acción: se infiere de los patrones del elemento (Invoke, Toggle, ExpandCollapse, SelectionItem, en ese orden) solo para el botón izquierdo; si no tiene ninguno, `coordinate_click`.
- Si entre el clic y el final de la lectura del elemento pasa más de `max_capture_delay_s`, el elemento se descarta.
- Atajo de pausa con `RegisterHotKey` de Win32, sin gancho de teclado. El gancho de teclado (pynput) solo existe si `capture_shortcuts` es verdadero. Sin atajo de pausa registrado, el crawler no arranca.
- Un atajo es al menos uno de Ctrl, Alt o Win más una tecla. Mayúsculas más tecla no cuenta, y con AltGr presionado tampoco (en teclado español escribe caracteres).
- Parada entre procesos con un evento con nombre de Win32; estado y contadores en `passive.status_path`, solo con números.
- CPU: se mide con `time.process_time` (todos los hilos del proceso) sobre tiempo de pared. El presupuesto del 5 % se compara con la fracción de un núcleo, que es la lectura más estricta; también se reporta la fracción de toda la máquina.
- Claves nuevas en `crawler.yaml`, sección `passive`: `stability_poll_s`, `resync_interval_s`, `resync_max_interval_s`, `max_capture_delay_s`, `status_path` y `status_interval_s`.
- Dependencia instalada en `.venv` para este paso: pynput 1.8.2, que ya estaba declarada en el grupo `crawler`. No se añade psutil.

**Motivo:**
- El gancho de mouse de bajo nivel corre dentro del sistema de entrada de Windows: si el callback tarda, se retrasa el mouse de todo el escritorio y Windows puede retirar el gancho. Por eso ahí no se llama a UI Automation.
- Un volcado puede tardar cientos de milisegundos. Si la lectura del clic compartiera hilo con los volcados, llegaría tarde. Se captura al presionar porque casi todas las interfaces reaccionan al soltar.
- Volcar el árbol en cada sondeo no cabe en el presupuesto de CPU; consultar la ventana en primer plano casi no cuesta.
- Ante la duda no se registra: una transición mal atribuida ensucia el grafo más de lo que aporta.

**Alternativas descartadas:**
- Leer el elemento dentro del callback de pynput (retrasa la entrada del sistema).
- Un solo hilo para captura y sondeo (la captura queda detrás de los volcados).
- Volcar el árbol en cada sondeo (CPU).
- `pynput.keyboard.GlobalHotKeys` para la pausa (instala un gancho de teclado aunque no se capturen atajos).
- psutil para medir CPU (dependencia nueva; `time.process_time` basta).
- Aceptar el estado intermedio cuando llega un segundo clic durante la espera (no hay garantía de que fuera estable).

**Limitaciones conocidas:**
- Los menús clásicos de Win32 son ventanas emergentes aparte: un clic en uno de sus elementos no se atribuye, y el menú abierto no aparece en el volcado de la ventana principal.
- Clics más rápidos que la ventana de estabilidad no dejan transición.
- Cambios hechos solo con el teclado, con `capture_shortcuts` apagado, se ven al resincronizar: se registra el estado, sin transición.
- El texto del atajo no se guarda: la tabla `transitions` no tiene dónde. Con `capture_shortcuts` activo la transición queda con acción `unknown` y sin elemento.
- Los volcados parciales no se registran, así que una app que siempre supere `max_nodes` o `dump_timeout_s` no deja estados.
- El estado de origen puede estar desactualizado hasta `resync_interval_s` si la interfaz cambió sin clic.

## D013. Grafo y consultas del crawler (2026-11-12)

**Decisión:**
- `networkx.MultiDiGraph` con un nodo por estado y una arista por fila de `transitions`. Se construye en memoria cada vez, con la interfaz pública de `CrawlerStore`, sin SQL propio.
- Una transición es ejecutable si tiene elemento y su acción es `invoke`, `toggle`, `expand_collapse`, `select` o `coordinate_click`. Quedan fuera `unknown` y `set_value`. Las rutas solo usan transiciones ejecutables.
- Coste de una transición: 1, salvo `coordinate_click`, que cuesta `graph.coordinate_click_cost` (clave nueva, 3.0 provisional). Entre dos estados se usa una sola arista: menor coste, luego mayor conteo, luego menor id. La ruta se calcula con Dijkstra.
- `path_to` devuelve las transiciones de la ruta más un paso final sobre el elemento objetivo. Con un objetivo de texto, los candidatos son los elementos que empatan en la mejor puntuación; gana el alcanzable de menor coste.
- Búsqueda difusa con `rapidfuzz.fuzz.WRatio` sobre texto normalizado (minúsculas, sin tildes, espacios simples) y el umbral `graph.fuzzy_match_threshold`. Vale para elementos y para el nombre de la app.
- Cobertura de elementos: accionables ejercitados entre accionables. Accionable es habilitado y con alguno de los patrones Invoke, Toggle, ExpandCollapse o SelectionItem. Las transiciones cuentan en la app del estado de origen.
- El grafo describe rutas y no aplica la lista de bloqueo de `safety.yaml`: esa puerta es del ejecutor.
- Dependencias instaladas en `.venv` para este paso: networkx 3.7 y rapidfuzz 3.14.6, ya declaradas en el grupo `crawler`.

**Motivo:** las bases del crawler son pequeñas y reconstruir el grafo en cada consulta es más simple que mantenerlo sincronizado. Penalizar el clic por coordenadas hace que las rutas prefieran los patrones de UI Automation, que es el orden de resolución de la sección 6.9.

**Alternativas descartadas:** consultas SQL recursivas (duplican la lógica de rutas fuera de networkx); guardar el grafo en disco (otra copia que sincronizar); filtrar en el grafo los elementos destructivos (duplicaría la puerta de seguridad y daría a entender que una ruta devuelta es segura).

**Limitaciones conocidas:**
- La búsqueda de rutas no se limita a los estados de la app pedida: si hay transiciones entre apps, una ruta puede pasar por otra app.
- `query` y `coverage` abren la base con `CrawlerStore`, que aplica migraciones pendientes y usa el modo WAL: no es una apertura de solo lectura estricta, aunque no cambia filas.
- Las transiciones que el crawler pasivo guarda sin elemento (clics en contenido de listas, atajos) no sirven para rutas.

## D014. Exportación de notas a Obsidian (2026-11-13)

**Decisión:**
- Una nota por app en `<bóveda>/<export.notes_folder>/<ejecutable sin .exe>.md`. El nombre sale del ejecutable, no del nombre visible, porque el ejecutable es estable.
- Un único bloque administrado por nota, entre `managed_block_start` y `managed_block_end`. Un marcador solo cuenta si ocupa una línea entera.
  - Nota nueva: título `# <nombre visible>` fuera del bloque, y después el bloque.
  - Nota existente sin marcadores: el bloque se añade al final.
  - Nota con un bloque: se reemplaza solo el bloque; el resto queda byte a byte, con sus saltos de línea (`\n` o `\r\n`) y su BOM.
  - Marcadores que no forman un único bloque válido, o nota que no es UTF-8: la nota no se toca y se reporta como error. Las demás apps se exportan igual.
- El bloque es determinista y no lleva fecha de exportación. Si el texto resultante es igual al que hay, no se escribe.
- Escritura atómica: archivo temporal en la misma carpeta y `os.replace`. Nunca se borra, mueve ni renombra una nota.
- La bóveda debe existir y el código nunca la crea. La carpeta de notas debe ser relativa y quedar dentro de la bóveda.
- `export.obsidian_vault_path` sigue como `TODO`: sin ese dato y sin `--vault`, la exportación falla con `ConfigIncompleteError`, antes de abrir la base.
- Una app que hoy está en `apps.excluded` no se exporta, aunque la base tenga datos antiguos de ella.
- Los nombres de apps, vistas y elementos pasan por `clean_inline` (espacios simples, `|` y `<` escapados), para que un nombre no rompa una tabla ni forme una línea que sea un marcador.
- Contenido del bloque: resumen, vistas conocidas, acciones frecuentes (transiciones ejecutables por conteo, hasta `top_actions_per_app`) y rutas a acciones clave.
- Acciones clave: los elementos accionables de los estados que no son la vista de partida (el estado con más visitas) y que tienen ruta ejecutable desde ella, sin repetir nombre, por coste de ruta, hasta `top_routes_per_app` (clave nueva, 20 provisional).
- El grafo gana lectores públicos (`apps`, `states_of`, `elements_of`, `transitions_of`, `element`, `state`, `is_route_edge`) y las funciones `element_label` y `describe_action`.

**Motivo:** la bóveda contiene notas escritas por Manuel. Ante una nota dudosa, lo seguro es no escribir. Sin fecha en el bloque, exportar dos veces seguidas no cambia el archivo, y eso permite comprobar la idempotencia y no ensuciar la sincronización de la bóveda.

**Alternativas descartadas:** un bloque por sección (más marcadores que se pueden romper al editar); nombre de archivo a partir del nombre visible (puede cambiar y dejar notas huérfanas); reparar marcadores mal formados (obliga a adivinar qué texto es de Manuel); poner la fecha de exportación en el bloque (cada exportación reescribiría todas las notas).

**Limitaciones conocidas:**
- Los nombres de elementos se copian a la bóveda. Si la bóveda se sincroniza con la nube, esos nombres salen del PC.
- No se manejan los nombres de archivo reservados de Windows (`con`, `nul`) ni dos ejecutables que queden con el mismo nombre saneado.
- En las rutas, todos los elementos sin nombre cuentan como el mismo y solo aparece uno.
- Se calcula una ruta por cada elemento accionable: con apps de miles de elementos puede ir lento.
- No se limpian las notas de apps que ya no están en la base.

## D015. Lanzador y acciones directas (2026-11-14)

**Decisión:**
- Todo lo que toca el sistema está en `actions/system.py`, en cinco funciones: abrir un `.lnk`, abrir una app por AppID, abrir el explorador, pulsar una tecla multimedia y leer `Get-StartApps`. Los demás módulos las llaman como atributos del módulo, y los tests rápidos las sustituyen todas con un fixture automático.
- `SendInput` solo puede enviar las seis teclas de `MEDIA_KEYS`. No hay ninguna función que acepte un código de tecla. Es el único uso de `SendInput` en el proyecto.
- Índice de apps: archivos `.lnk` de `launcher.start_menu_dirs` más la salida de `Get-StartApps`. Dos entradas con el mismo nombre normalizado son la misma app y gana el acceso directo. No se resuelve el destino de los `.lnk`.
- Caché del índice en `launcher.cache_path`, válida `cache_max_age_s`. Se reconstruye si falta, es antigua, está corrupta o se pide con `--refresh`. Si `Get-StartApps` falla, el índice sigue solo con los accesos directos.
- Coincidencia: exacta sobre el nombre normalizado o `rapidfuzz.fuzz.WRatio` con el umbral `launcher.fuzzy_match_threshold`. Una única coincidencia exacta gana. Si la segunda candidata queda a menos de `ambiguity_margin` de la primera, o hay dos exactas, el resultado es ambiguo: se devuelven hasta `max_options` opciones y no se abre nada.
- Solo se lanzan entradas del índice. Nunca una ruta ni un comando escrito por el usuario.
- La lista de bloqueo de `safety.yaml` se aplica también al lanzador, aunque la orden venga de la CLI: una app cuyo nombre contiene un patrón destructivo no se abre.
- Las acciones directas solo se hacen si su clave está en `launcher.direct_actions`.
- Auditoría en `safety.crawler.audit_log.path`, en JSON Lines: una línea por acción hecha o rechazada, con acción, objetivo, app, resultado y quién la autorizó. La CLI firma como `cli:manuel`. Una simulación (`--dry-run`) no se audita.
- Claves nuevas en `crawler.yaml`, sección `launcher`: `cache_path`, `cache_max_age_s`, `start_apps_timeout_s`, `ambiguity_margin` y `max_options`.

**Motivo:** es el primer código del crawler que actúa sobre el escritorio. Concentrar el contacto con el sistema en un módulo pequeño y de lista cerrada permite revisarlo entero y garantizar que los tests rápidos no pueden actuar. Ante la duda, devolver opciones es mejor que abrir la app equivocada.

**Alternativas descartadas:** resolver el destino de los `.lnk` y lanzar el ejecutable (abre la puerta a lanzar rutas arbitrarias); elegir siempre la mejor puntuación (adivina cuando hay empate); `keybd_event` o pynput para las teclas multimedia (API antigua, o una dependencia capaz de enviar cualquier tecla); no aplicar la lista de bloqueo a órdenes de la CLI (un nombre aproximado podría acabar abriendo un desinstalador).

**Limitaciones conocidas:**
- La lista de bloqueo compara por subcadena y da falsos positivos: en este PC bloquea "System Information" y una referencia de RivaTuner porque contienen "format". Va del lado seguro; falta decidir si se compara por palabra completa.
- La ambigüedad depende de lo instalado.
- `launch` no espera a que la app abra ni comprueba que abrió: `launched` significa que se pidió la apertura a Windows. En la verificación del 2026-11-14 un lanzamiento de Configuración dio `launched` y la app no se abrió; al segundo intento sí. La causa no se encontró. La comprobación y el reintento quedan para el ejecutor (sección 6.9).
- Si la app pide elevación (UAC), no se hace nada más.
- Las teclas multimedia se envían sin `KEYEVENTF_EXTENDEDKEY`. Comprobado con TIDAL: `play_pause` pausa y reanuda.
- La CLI repite la lista de acciones multimedia (`MediaChoice`) para no importar el paquete al arrancar; un test comprueba que coincide con `MediaAction`.
