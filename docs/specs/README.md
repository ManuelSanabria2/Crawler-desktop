# Especificaciones

Cada tarea que el orquestador (la sesión principal) delega al subagente `implementer` se describe antes en un archivo de esta carpeta. El subagente no ve la conversación: solo lee `CLAUDE.md`, la especificación y el código. Lo que no esté escrito aquí, no existe para él.

## Nombre de los archivos

- Un archivo por tarea delegada: `docs/specs/<ID>.md`.
- `<ID>` es el identificador del paso de `PROMPT_CHAIN.md`: `P01`, `C04`, `A03`. Las tareas de orquestación que no son un paso de la cadena usan el prefijo `O`: `O00`, `O01`.
- Si un paso se delega en varias partes, se añade una letra: `C04a.md`, `C04b.md`. Cada parte debe poder implementarse y verificarse sola.
- `_plantilla.md` es la plantilla. Los archivos que empiezan por `_` no son tareas.

## Cómo se escribe una especificación

1. Copia `_plantilla.md` a `<ID>.md` y rellena todas las secciones. Si una sección no aplica, se escribe "No aplica" y el motivo; no se borra.
2. Escríbela para alguien que no conoce la conversación:
   - rutas completas de archivos, no "el módulo de antes";
   - firmas exactas con tipos, no descripciones de la firma;
   - valores concretos en los casos de prueba.
3. Cita las secciones de `INSTRUCCIONES.md` y los archivos existentes que el implementador debe leer.
4. Las decisiones de diseño las toma el orquestador y van escritas. El implementador no decide interfaces: si una no funciona, se detiene y lo reporta.
5. Copia en "Reglas de seguridad aplicables" las reglas de `CLAUDE.md` que toca la tarea. Si no toca ninguna, se dice.
6. Los criterios de aceptación deben poder comprobarse con un comando o leyendo el diff.
7. Ningún dato físico inventado: si la tarea necesita un dato que sigue como `TODO` en `config/`, la especificación dice cómo debe fallar el código, no qué valor usar.

## Ciclo de una tarea delegada

1. El orquestador escribe `docs/specs/<ID>.md`.
2. Delega al subagente `implementer` indicando solo la ruta de la especificación.
3. El subagente implementa, corre las verificaciones y entrega su reporte: archivos, resultados, desviaciones y preguntas abiertas.
4. El orquestador revisa el diff completo contra la especificación, vuelve a correr él mismo los tests y las verificaciones, y pide correcciones si hacen falta.
5. El orquestador cierra el paso: marca la casilla en `PROMPT_CHAIN.md`, añade la entrada a `docs/BITACORA.md` y registra las decisiones nuevas en `docs/DECISIONES.md`. El subagente nunca edita esos archivos ni las especificaciones.

Una especificación no se reescribe después de implementada. Si la tarea cambia, se anota al final, en "Cambios", con fecha y motivo.
