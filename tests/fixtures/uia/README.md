# Volcados de UI Automation para tests

Volcados reales de `dump_window`, guardados para que los tests de firmas no necesiten un escritorio de Windows.

| Archivo | Qué es |
|---|---|
| `calculator_a.json`, `calculator_b.json` | La Calculadora en modo estándar, volcada dos veces seguidas |
| `notepad_short.json` | Bloc de notas con un archivo de una línea |
| `notepad_long.json` | Bloc de notas con otro archivo de 120 líneas (aparecen barras de desplazamiento) |
| `settings_mouse.json` | Configuración, página Bluetooth y dispositivos, Mouse |
| `settings_colors.json` | Configuración, página Personalización, Colores |

Se generan con `python scripts/capture_uia_fixtures.py` en Windows 11 en español. El script tacha el nombre de usuario, el nombre del equipo y los correos antes de guardar, y sustituye el nombre de las pestañas del Bloc de notas que no abrió él. Un test comprueba que no queden correos ni el nombre de usuario.

No contienen el valor de ningún campo de texto: el lector no lo lee.

Si se vuelven a capturar, las firmas pueden cambiar con la versión de Windows o de las apps. Los tests comparan firmas entre fixtures, no contra valores fijos.
