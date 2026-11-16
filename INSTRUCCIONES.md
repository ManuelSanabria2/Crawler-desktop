# GEASS: especificación del proyecto

Versión 1.0 (noviembre de 2026). Autor: Manuel Jose Sanabria Gil. Construido con Claude Code.

## 1. Visión

GEASS es la IA personal de Manuel. Esta versión cubre dos capacidades que viven en un mismo repositorio:

1. **Controlar un brazo robótico de 6 ejes que juega ajedrez.**
   - GEASS corre en el PC: ve el tablero, decide la jugada y planea el movimiento.
   - El ESP32 solo actúa como puente que ejecuta las órdenes de movimiento.
2. **Operar el escritorio de Windows.**
   - Un crawler explora y mapea las aplicaciones mediante UI Automation, como un web crawler mapea páginas.
   - Con ese mapa, más un conjunto de acciones directas, GEASS puede:
     - abrir apps y el explorador;
     - controlar la música;
     - navegar dentro de las aplicaciones;
     - pulsar botones de autorización cuando Manuel se lo ordene.

### Qué es "IA propia" en este proyecto

GEASS no es un modelo de lenguaje entrenado desde cero: eso no es viable con este hardware ni con datos de una sola persona. Lo propio son los modelos especializados que Manuel entrena:

- **Red neuronal de cinemática inversa (IK neuronal):** aprende a llevar la garra a una pose objetivo.
- **Red de calibración aprendida:** corrige con datos reales los errores del brazo físico, como el juego mecánico de los servos y las tolerancias de impresión.
- **Clasificador de casillas del tablero:** reconoce qué pieza hay en cada casilla.
- **Detector visual de elementos de interfaz:** encuentra botones y campos en apps que no exponen accesibilidad. Se entrena con datos que el propio crawler etiqueta automáticamente.

La IK analítica se mantiene siempre como referencia de seguridad y respaldo.

## 2. Alcance

### Incluido en esta versión

- Componente brazo: cinemática, simulación, IK neuronal, seguridad, planificación, protocolo, firmware, percepción del tablero, juego completo y calibración aprendida.
- Componente crawler: lector UIA, firmas de estado, almacenamiento, crawler pasivo, grafo, exportación a Obsidian, lanzador y acciones directas, ejecutor con puerta de seguridad, crawler activo en sandbox, dataset visual y detector visual.

### Fuera de alcance por ahora (integración posterior)

- Activación por voz y reconocimiento de voz.
- Modelo de lenguaje como planificador y RAG general sobre Obsidian. Este proyecto solo exporta notas que el RAG podrá usar después.
- Interfaz Electron.
- Aprendizaje por refuerzo para el brazo. Solo se considerará si la calibración aprendida no alcanza la precisión requerida.

Todo módulo debe exponer una API Python limpia (funciones y clases) además de su CLI. Así, la capa de voz y el planificador podrán llamarlo más adelante sin reescribir nada.

## 3. Arquitectura general

```
                  +-------------------------------- PC (GEASS) --------------------------------+
                  |                                                                             |
 Cámara  -------> |  perception -> chess (python-chess + Stockfish) -> planning -> safety -----> | --USB serial--> ESP32
                  |                         ^                                                   |                |
                  |                   neural_ik / IK analítica / calibration                    |       PCA9685 (servos)
                  |                                                                             |       Driver NEMA17
                  |  crawler: uia -> signature -> store (SQLite) -> graph -> export (Obsidian)  |
                  |           passive (observa)   active (sandbox)   actions (ejecutor + seguridad)
                  +-----------------------------------------------------------------------------+
```

Principios:

- **El cerebro está en el PC.** El ESP32 recibe poses articulares o secuencias cortas y las interpola localmente, con sus propios temporizadores. Así la latencia de la comunicación no afecta la suavidad del movimiento.
- **El transporte es intercambiable.** Primero USB serial; WiFi después, con el mismo protocolo de mensajes.
- **Todo se valida antes de actuar.** Tanto el brazo como el ejecutor del escritorio pasan por una capa de seguridad determinista, nunca por un modelo aprendido.

## 4. Hardware del brazo (versión v5)

Estado conocido:

- **Mecánica:** brazo articulado de 6 ejes estilo cobot con muñeca esférica, diseñado en Fusion para impresión 3D.
  - Base con motor NEMA17.
  - Garra de tres dedos.
- **Actuadores:** servos MG996 y MG90 (la asignación por eje está pendiente) más un NEMA17 en la base.
- **Electrónica:**
  - ESP32.
  - PCA9685 para el PWM de los servos.
  - Driver de paso a paso para el NEMA17 (modelo pendiente, por ejemplo TMC2209 o A4988).
  - Módulo LM2596.
  - Baterías 3.7 V en configuración 2S con BMS (la entrada de carga está pendiente de resolver).

### Advertencias de hardware

- **Alimentación de los servos.** Varios MG996 pueden pedir más de 2 A cada uno en bloqueo, y el LM2596 típico entrega unos 3 A. Hace falta:
  - una fuente dedicada de 5 a 6 V con corriente suficiente para todos los servos;
  - tierra común con el ESP32 y el driver;
  - un capacitor grande en la entrada del PCA9685.
- **Homing del NEMA17.** Sin un final de carrera, la base no puede saber su posición al encender. Opciones:
  - añadir un final de carrera (recomendado);
  - poner a cero manualmente en una marca física cada vez.
- **Juego mecánico.** Los servos de hobby tienen holgura de 1 a 2 grados y no reportan su posición. La precisión final dependerá de la calibración aprendida (sección 5.9), no solo del modelo cinemático.

## 5. Componente 1: IA del brazo

### 5.1 Modelo cinemático

- Parámetros Denavit-Hartenberg (o equivalente) en `config/arm.yaml`, tomados del modelo de Fusion. Pendiente de Manuel.
- Cinemática directa (FK) implementada dos veces:
  - en NumPy, para uso general;
  - en PyTorch, diferenciable, para la función de pérdida de la red.

  Ambas deben coincidir en menos de 1e-9 m.
- IK analítica por desacople de muñeca esférica:
  - las tres primeras articulaciones ubican el centro de la muñeca;
  - las tres últimas dan la orientación.
- La IK analítica enumera todas las soluciones y elige según:
  1. la rama configurada (por ejemplo, codo arriba y muñeca sin voltear);
  2. la cercanía a la configuración actual;
  3. los límites articulares.

  Si no hay solución alcanzable, devuelve `None`.

### 5.2 Simulación

- **Motor: MuJoCo.** Se elige sobre PyBullet porque tiene wheels oficiales y mantenidos para Windows, buen render fuera de pantalla y física de contacto estable.
- **Modelo:**
  - MJCF generado desde los parámetros de `arm.yaml`, con geometrías simples;
  - opcionalmente, mallas exportadas de Fusion en `hardware/`.
- **Actuadores** de posición con límites y velocidades de `arm.yaml`.
- **Escena de ajedrez:**
  - tablero con medidas de `board.yaml`;
  - piezas simplificadas (cilindros o mallas sencillas con alturas por tipo);
  - zona de "cementerio" para piezas capturadas;
  - cámara virtual en la pose configurada.
- **Agarre:** fricción realista o, si da problemas, una restricción de soldadura que se activa al cerrar la garra. Debe documentarse cuál se usa.
- **Verificación de alcance:** el sistema debe confirmar que las 64 casillas y el cementerio son alcanzables con las alturas de aproximación y agarre. Si alguna no lo es, se reporta antes de seguir.

### 5.3 Dataset para la IK neuronal

- Muestreo en el espacio articular, dentro de los límites y de la rama configurada. Restringir la rama evita que una misma pose tenga varias respuestas válidas, lo que confunde a la red.
- Para cada muestra se calcula la FK y se guardan:
  - la posición (x, y, z);
  - la orientación en representación 6D (las dos primeras columnas de la matriz de rotación);
  - los ángulos articulares.
- Sobremuestreo de la región de trabajo real: sobre el tablero y el cementerio, a alturas de agarre y aproximación.
- Tamaño por defecto: 1 millón de muestras (configurable). Formato Parquet en `data/arm/ik/`. Divisiones de entrenamiento, validación y prueba con semilla fija.

### 5.4 Red neuronal de IK

- **Entrada (9 valores):** posición normalizada por el alcance más orientación 6D.
- **Salida (12 valores):** seno y coseno de cada articulación. El ángulo se recupera con atan2, lo que evita el salto entre -180 y 180 grados.
- **Arquitectura base:** MLP de 4 capas ocultas de 256 neuronas con activación SiLU. Se permiten variantes, registradas en `docs/DECISIONES.md`.
- **Pérdida:** combinación de:
  - error cuadrático en seno y coseno;
  - error de posición de la FK diferenciable sobre la salida, en metros;
  - error geodésico de orientación.

  Los pesos van en la configuración.
- **Entrenamiento:**
  - en CPU con parada temprana;
  - variante para Colab en `scripts/colab/`;
  - con 1 millón de muestras y este tamaño de red, debe ser viable en CPU en menos de una hora.
- **Metas en la región de trabajo (sobre la prueba simulada):**
  - error de posición: mediana menor a 2 mm y percentil 95 menor a 5 mm;
  - error de orientación: percentil 95 menor a 3 grados.
- **Reporte obligatorio:** comparación con la IK analítica en precisión y en tiempo por lote.

  Es esperable que la analítica sea más precisa. El valor de la red está en tres cosas:
  - el aprendizaje;
  - el cálculo en lote;
  - ser la base de la calibración aprendida.

### 5.5 Seguridad del brazo

Módulo `geass.arm.safety`. Para cualquier configuración articular candidata comprueba:

- límites articulares;
- FK para confirmar que la pose resultante coincide con la objetivo (umbral en `safety.yaml`);
- caja de trabajo permitida;
- altura mínima sobre el tablero, salvo en las fases de agarre y soltado;
- velocidad y aceleración máximas por articulación;
- opcionalmente, colisión en MuJoCo.

Selector de solución: red neuronal, luego validación; si falla, IK analítica, luego validación; si falla, se aborta con error explicativo.

### 5.6 Planificación

- **Pick and place** como secuencia de puntos de paso:
  1. aproximación sobre el origen;
  2. descenso;
  3. cierre de la garra;
  4. ascenso;
  5. traslado a altura segura;
  6. aproximación al destino;
  7. descenso;
  8. apertura;
  9. ascenso;
  10. retorno a una pose de espera.
- Interpolación cartesiana entre puntos con IK en cada subpaso. Tiempos sincronizados para que todas las articulaciones lleguen a la vez, con perfil trapezoidal.
- Casos especiales de ajedrez:
  - **Captura:** primero llevar la pieza capturada al cementerio.
  - **Enroque:** dos movimientos.
  - **Captura al paso:** retirar el peón capturado de su casilla real.
  - **Promoción:** GEASS avisa y pide a Manuel colocar la pieza, porque no hay piezas de repuesto en el tablero.

### 5.7 Protocolo PC y ESP32

**Formato:** JSON delimitado por salto de línea sobre serial a 115200 baudios. Cada mensaje lleva un `id` entero.

**Comandos del PC:**

| Comando | Campos | Descripción |
|---|---|---|
| `PING` | | Señal de vida (también alimenta el watchdog) |
| `HOME` | | Secuencia de inicio y homing |
| `MOVE_J` | `q` (grados, 6 valores), `t` (s) | Ir a una pose articular en el tiempo indicado |
| `SEQ` | `steps` (lista de `{q, t}`, máximo configurable) | Secuencia corta de poses |
| `GRIP` | `state` (`open` o `close`) | Garra |
| `STOP` | | Detención inmediata |
| `STATUS` | | Pide estado y pose comandada actual |

**Respuestas del ESP32:**

- `{"id": n, "ack": true}` al recibir y validar el comando.
- `{"id": n, "done": true}` al terminar.
- `{"id": n, "err": "<código>"}` ante cualquier error. Códigos: `PARSE`, `LIMIT`, `BUSY`, `WATCHDOG`, `STOPPED` y `NOT_HOMED`.

**Watchdog:** si no llega `PING` ni otro comando durante `watchdog_ms` (por defecto 1000), el ESP32 detiene el movimiento y responde `WATCHDOG` al siguiente mensaje.

**Implementación en el PC:**

- cliente con timeouts;
- reintento solo para comandos idempotentes (`PING`, `STATUS`);
- hilo de PING;
- capa de transporte abstracta;
- un **ESP32 simulado** (mock) que permite probar todo el flujo sin hardware.

### 5.8 Firmware ESP32

- PlatformIO con framework Arduino. Librerías: ArduinoJson, Adafruit PWM Servo Driver y AccelStepper.
- **Módulos:**
  - parser del protocolo;
  - control de servos por canal (calibración de pulso mínimo y máximo, offset e inversión);
  - control del NEMA17 (micropasos y relación de transmisión);
  - interpolador sincronizado;
  - watchdog;
  - manejo de `STOP`;
  - arranque seguro.
- **Calibración:** un script del PC genera `firmware/esp32/include/calibration.h` desde `arm.yaml`, para tener una sola fuente de verdad.
- **Arranque:** salidas de servos apagadas hasta `HOME`. Si no hay final de carrera, `HOME` asume que la base está en la marca física y lo registra.

### 5.9 Calibración aprendida

- Un marcador ArUco en la garra y la cámara permiten medir la pose real alcanzada para cada pose comandada.
- Se recolectan pares (pose comandada, pose medida) en la región de trabajo.
- Una red pequeña aprende la corrección residual. Así el brazo apunta donde de verdad debe.
- **Meta:** reducir al menos a la mitad el error medio de colocación real, medido sobre casillas no usadas en el entrenamiento.

### 5.10 Percepción del tablero

- **Localización del tablero:** cuatro marcadores ArUco en las esquinas, homografía y vista cenital normalizada. Recorte de las 64 casillas.
- **Clasificador de casillas:**
  - 13 clases: vacía más las 6 piezas por cada color;
  - CNN pequeña entrenada desde cero, comparada con MobileNetV3 pequeño preentrenado.
- **Datos sintéticos:** renders de MuJoCo con posiciones de partidas legales aleatorias y aleatorización de dominio (luz, texturas, ruido y pequeñas variaciones de cámara). Las etiquetas son automáticas.
- **Datos reales:** herramienta en la que Manuel coloca una posición conocida (FEN) y el sistema captura y etiqueta solo. Unas pocas centenas de capturas bastan para el ajuste fino.
- **Decodificación robusta:** la jugada humana se deduce comparando estados, restringida a las jugadas legales de python-chess. Se elige la jugada legal más consistente con las probabilidades del clasificador.
- **Metas:**
  - exactitud por casilla mayor al 99 % en datos reales de prueba;
  - detección correcta de la jugada humana en más del 98 % de los casos.

### 5.11 Ajedrez

- python-chess para las reglas. Stockfish como motor, con la ruta del ejecutable, el nivel y el tiempo por jugada en `config/board.yaml`.
- **Ciclo de juego:** detectar la jugada humana, consultar al motor, planear, validar y ejecutar.
  - Primero se juega en simulación (`--sim`).
  - Luego en el brazo real a velocidad reducida.

## 6. Componente 2: crawler de escritorio

### 6.1 Conceptos

- **Elemento:** nodo del árbol de UI Automation. Tiene tipo de control, nombre, AutomationId, clase, rectángulo, estado habilitado o visible, patrones soportados y ruta desde la raíz.
- **Estado:** una vista de una aplicación, identificada por una firma.
- **Acción:** operación sobre un elemento:
  - invocar;
  - alternar;
  - expandir o contraer;
  - seleccionar;
  - escribir valor (solo en ejecución y nunca autónomamente en campos sensibles);
  - clic por coordenadas (último recurso).
- **Transición:** estado de origen, acción, estado de destino, latencia y fuente (pasiva o activa).

### 6.2 Lector UIA

- pywinauto con backend `uia`; comtypes para eventos cuando haga falta.
- **Volcado** de la ventana activa o de una ventana por handle a un esquema Pydantic. Límites configurables de:
  - profundidad;
  - número de nodos;
  - tiempo.
- **Manejo robusto** de elementos que desaparecen durante la lectura y de errores COM. Un volcado parcial se marca como tal; nunca se presenta como completo.
- **Rendimiento:** volcar la ventana activa de una app común en menos de 1 s. Si no se logra, se documenta por qué.

### 6.3 Firmas de estado

Hay dos niveles:

- **Firma estructural:** identifica la vista. Es un hash de:
  - el proceso;
  - la clase de ventana;
  - el multiconjunto de tipos de control y AutomationId, o nombres normalizados, por niveles de profundidad.
- **Firma detallada:** distingue variantes de la misma vista.

Normalización:

- se ignoran los textos variables (números, fechas, contenidos de listas y documentos);
- los contenedores volátiles (listas, cuadrículas, documentos) se colapsan a su tipo más un rango de cantidad;
- se ignora el título de la ventana más allá de un patrón (por ejemplo, el nombre de la app sin el nombre del archivo abierto).

Pruebas de aceptación:

- la misma vista visitada dos veces da la misma firma estructural en al menos 95 % de los casos;
- vistas distintas dan firmas distintas.

### 6.4 Almacenamiento

SQLite en `data/crawler/geass_crawler.sqlite` con tabla de versión de esquema. Tablas:

| Tabla | Campos |
|---|---|
| `apps` | id, ejecutable, nombre visible, AppUserModelId opcional |
| `states` | id, app, firma estructural, firma detallada, patrón de título, primera y última vez vista, visitas, ruta de captura opcional |
| `elements` | id, estado, ruta, tipo, nombre, AutomationId, rectángulo relativo a la ventana, patrones, habilitado |
| `transitions` | id, estado origen, estado destino, elemento, tipo de acción, fuente, conteo, latencia media, última vez |
| `sessions` | id, inicio, fin, modo, notas |

Capturas en `data/crawler/screens/`, deduplicadas con hash perceptual y con política de retención configurable.

### 6.5 Crawler pasivo

- **Observación** de la ventana en primer plano (sondeo configurable más eventos de UIA cuando sea posible).
- **Escucha del mouse** (pynput). Al presionar, registra el elemento bajo el cursor (`from_point`) antes de que la interfaz cambie.
- **Espera de estabilidad:** el árbol se considera estable cuando no cambia durante una ventana configurable (por defecto 400 ms) o al superar un tiempo límite. Entonces registra el nuevo estado y la transición.
- **Privacidad:**
  - apps excluidas;
  - campos de contraseña ignorados;
  - sin valores de texto;
  - atajos de teclado solo si `capture_shortcuts` está activo.
- **Control:** atajo global de pausa y reanudación, más comandos de CLI para iniciar, detener y ver estadísticas.
- **Presupuesto de CPU:** promedio menor al 5 % en el PC de desarrollo.

### 6.6 Grafo y consultas

- Grafo dirigido con networkx, construido desde SQLite.
- Consultas:
  - búsqueda difusa de elementos por nombre dentro de una app (rapidfuzz);
  - ruta más corta desde el estado actual hasta el estado que contiene el elemento objetivo;
  - estadísticas de cobertura por app.

### 6.7 Exportación a Obsidian

- Una nota por aplicación en la carpeta configurada de la bóveda. Contenido:
  - vistas conocidas;
  - acciones frecuentes;
  - rutas a acciones clave (por ejemplo, "Exportar a PDF: Archivo, luego Exportar, luego PDF").
- **Actualización idempotente:** el contenido generado va entre marcadores de bloque administrado. Lo que Manuel escriba fuera de esos bloques nunca se sobrescribe.

### 6.8 Lanzador y acciones directas

- **Índice de apps:**
  - accesos directos del menú Inicio (ProgramData y AppData);
  - apps de la tienda (salida de `Get-StartApps` en PowerShell).
- **Lanzamiento** por coincidencia difusa del nombre.
- **Acciones directas** que no requieren navegar la interfaz:
  - abrir el explorador (opcionalmente en una ruta);
  - teclas multimedia: reproducir o pausar, siguiente, anterior, volumen.

### 6.9 Ejecutor y puerta de seguridad

**Orden de resolución de una acción:**

1. acción directa;
2. elemento en la ventana actual por AutomationId o nombre, usando su patrón UIA;
3. navegación por ruta del grafo;
4. detector visual;
5. clic por coordenadas.

**Verificación posterior:** se comprueba que ocurrió la transición esperada. Si no, se reintenta una vez y luego se reporta el fallo.

**Clasificación de cada acción según `safety.yaml`:**

- **Libre:** se ejecuta.
- **Requiere confirmación:** se pide confirmación a Manuel mediante un callback. En esta versión es la CLI; después será la voz.
- **Prohibida:** nunca se ejecuta.

**Botones de autorización:** se ejecutan solo con una orden explícita y actual de Manuel para esa acción concreta, y solo en las ventanas listadas en la configuración.

**Benchmark:** `tests/benchmarks/crawler_tasks.yaml` con 20 a 30 tareas (abrir apps, música, explorador, navegar a configuraciones). Metas de tasa de éxito:

- 95 % en acciones directas;
- 80 % en navegación dentro de apps con buena accesibilidad.

### 6.10 Crawler activo (solo en sandbox)

- **Entorno:**
  - Windows Sandbox (requiere Windows Pro o Enterprise) con un archivo `.wsb` en `sandbox/`;
  - el repo se monta solo lectura y una carpeta de salida en escritura;
  - alternativa para Windows Home: máquina virtual con instantánea limpia.
- **Exploración en anchura** sobre elementos seguros:
  - menús, pestañas, elementos de árbol expandibles e hipervínculos internos;
  - botones solo si su nombre está en una lista permitida.
- **Límites:** profundidad máxima, acciones por app y tiempo por app.
- **Reglas:**
  - manejo de diálogos con Escape o Cancelar;
  - reinicio de la app al perder el rastro;
  - nunca escribe texto.
- Los resultados se fusionan en la base principal con fuente `active`.

### 6.11 Dataset visual y detector

- **Dataset:** a partir de las capturas y los rectángulos de los elementos se genera un dataset en formato COCO.
  - Filtros: elementos visibles, área mínima, dentro de la ventana y sin oclusión evidente.
  - Clases agrupadas: botón, campo de texto, casilla, elemento de menú, pestaña, enlace e icono u otro.
- **División por aplicación:** las apps de prueba no aparecen en entrenamiento. Así se mide la generalización real.
- **Detector:** de torchvision (licencia BSD), por ejemplo SSDlite con MobileNetV3 o Faster R-CNN con MobileNetV3.
  - Entrenamiento en Colab e inferencia en CPU.
  - Meta orientativa: mAP@0.5 de 0.6 o más en apps no vistas. Se reporta por clase.
- **Integración:** como respaldo en el ejecutor cuando UIA no expone el elemento.

## 7. Estructura del repositorio

```
geass/
  CLAUDE.md
  INSTRUCCIONES.md
  PROMPT_CHAIN.md
  README.md
  pyproject.toml
  config/
    arm.yaml            parámetros cinemáticos, límites, actuadores, calibración
    arm.test.yaml       valores ficticios solo para tests
    board.yaml          tablero, piezas, cementerio, cámara, Stockfish
    safety.yaml         umbrales del brazo, listas de bloqueo y confirmación del crawler
    crawler.yaml        sondeo, privacidad, apps excluidas, rutas, Obsidian
  src/geass/
    core/               config, logging, rutas, unidades
    arm/
      kinematics/       DH, FK numpy y torch, IK analítica
      sim/              MuJoCo: modelo, escena, cámara, render
      neural_ik/        dataset, modelo, entrenamiento, evaluación
      calibration/      calibración aprendida con datos reales
      safety/           validación y selector de soluciones
      planning/         trayectorias pick and place
      perception/       ArUco, homografía, clasificador de casillas
      chess/            reglas, motor, traducción jugada a plan
      bridge/           protocolo, cliente, transporte, ESP32 simulado
    crawler/
      uia/              lectura del árbol y esquema
      signature/        firmas de estado
      store/            SQLite y capturas
      passive/          observador
      graph/            grafo y consultas
      export/           notas para Obsidian
      actions/          lanzador, acciones directas, ejecutor, seguridad
      active/           explorador para sandbox
      vision/           dataset visual y detector
  firmware/esp32/       PlatformIO
  hardware/             MJCF o URDF, mallas exportadas de Fusion
  scripts/colab/        entrenamientos en Colab
  sandbox/              .wsb y scripts de arranque
  tests/                misma estructura que src, más benchmarks/
  docs/
    BITACORA.md
    DECISIONES.md
    PRUEBAS_HARDWARE.md
  data/                 fuera de git
  models/               fuera de git
```

## 8. Stack y dependencias

- **Base:** Python 3.11, numpy, scipy, pydantic, pyyaml, typer (CLI), rich y pandas con pyarrow.
- **Grupo `arm`:**
  - torch (CPU), mujoco y opencv-contrib-python (ArUco);
  - python-chess y pyserial;
  - Stockfish como ejecutable externo.
- **Grupo `crawler`:**
  - pywinauto, comtypes y pywin32;
  - pynput y mss;
  - pillow, imagehash, networkx y rapidfuzz;
  - torchvision para la parte visual.
- **Grupo `dev`:** pytest y ruff.
- **Firmware:** ArduinoJson, Adafruit PWM Servo Driver y AccelStepper.

El grupo `crawler` solo funciona en Windows. El grupo `arm` debe funcionar también en Linux, para poder entrenar en Colab.

## 9. Criterios de aceptación globales

- Todos los tests no marcados pasan en el PC de desarrollo.
- El brazo juega una partida completa en simulación sin intervención, salvo las promociones.
- El brazo mueve piezas reales con un error de colocación que no hace caer ni empujar piezas vecinas, en 20 movimientos seguidos.
- El crawler pasivo corre una jornada completa sin superar el presupuesto de CPU y sin registrar datos excluidos.
- El benchmark del ejecutor cumple las metas de la sección 6.9.
- Cada modelo entrenado tiene su `MODEL_CARD.md` con métricas y limitaciones.

## 10. Datos pendientes de Manuel

Estos datos deben quedar como `TODO` en `config/` hasta que Manuel los proporcione. El código que los use debe fallar con un mensaje claro.

| Dato | Dónde va |
|---|---|
| Parámetros DH o dimensiones de cada eslabón de la v5 (desde Fusion) | `arm.yaml` |
| Rango de cada articulación | `arm.yaml` |
| Qué actuador mueve cada eje y cuántos servos usa la garra | `arm.yaml` |
| Driver del NEMA17, micropasos y relación de transmisión de la base | `arm.yaml` |
| Si habrá final de carrera para el homing de la base | `arm.yaml` |
| Pines del ESP32 (I2C del PCA9685, STEP, DIR, EN, final de carrera) | `arm.yaml` |
| Fuente de alimentación de los servos y estado del BMS 2S | `docs/PRUEBAS_HARDWARE.md` |
| Tamaño de casilla, posición del tablero respecto a la base, alto y diámetro de las piezas | `board.yaml` |
| Cámara: modelo, resolución y montaje | `board.yaml` |
| Ruta del ejecutable de Stockfish | `board.yaml` |
| Edición de Windows (Home o Pro), para decidir entre Sandbox y máquina virtual | `crawler.yaml` |
| Ruta de la bóveda de Obsidian | `crawler.yaml` |
| Apps a excluir del crawler y apps prioritarias | `crawler.yaml` |
| Ventanas donde se permiten botones de autorización bajo orden explícita | `safety.yaml` |

## 11. Hoja de ruta

El detalle está en `PROMPT_CHAIN.md`. Orden recomendado:

1. **Base común:** P00 y P01.
2. **Logros rápidos del crawler:** C01 a C08. GEASS ya puede abrir apps, controlar la música, abrir el explorador y navegar apps con buena accesibilidad.
3. **Brazo en simulación:** A01 a A08.
4. **Brazo físico:** A09 y A10.
5. **Visión y juego:** A11 y A12.
6. **Crawler activo y visión de interfaz:** C09 a C11.
7. **Calibración aprendida:** A13.
8. **Cierre:** I01, integración y revisión general.
