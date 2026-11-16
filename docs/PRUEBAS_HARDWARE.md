# Pruebas de hardware

Registro de las pruebas con el brazo real, el ESP32 y la electrónica. Toda prueba se hace primero en simulación y luego en el brazo a la velocidad reducida de `config/safety.yaml`.

## Datos pendientes de Manuel

| Dato | Valor |
|---|---|
| Fuente de alimentación de los servos (tensión en V y corriente máxima en A) | TODO |
| Estado del BMS 2S y de la entrada de carga | TODO |

Advertencias de la sección 4 de `INSTRUCCIONES.md` a comprobar antes de energizar:

- [ ] Fuente dedicada de 5 a 6 V con corriente suficiente para todos los servos.
- [ ] Tierra común entre la fuente, el ESP32 y el driver.
- [ ] Capacitor grande en la entrada del PCA9685.
- [ ] Homing de la base resuelto: final de carrera o marca física.

## Plantilla

```
## AAAA-MM-DD. <nombre de la prueba>

**Objetivo:** ...
**Montaje:** firmware (commit), fuente, velocidad usada
**Procedimiento:** ...
**Resultado:** medidas y observaciones
**Incidencias:** ...
**Siguiente paso:** ...
```

## Pruebas

Todavía no hay pruebas registradas.
