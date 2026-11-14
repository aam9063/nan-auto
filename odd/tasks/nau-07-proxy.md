# NAU-07 y NAU-08: proxy `chat/completions` y streaming SSE

## Especificación (literal)

Del spec, sección 14:

> NAU-07 | Proxy `chat/completions` sin streaming | Cliente OpenAI funciona con `nan-auto` | **Alto**
> NAU-08 | Streaming SSE de extremo a extremo y cabeceras `x-nan-auto` | Cursor u OpenCode funcionan con `nan-auto` | **Alto**

De la sección 8:

| Método y ruta | Descripción |
|---|---|
| `POST /v1/chat/completions` | Compatible con OpenAI. Con `model: "nan-auto"`, `"nan-auto-fast"` o `"nan-auto-quality"` enruta. Con otro modelo, reenvía sin tocar nada |

> - `x-nan-auto-model`: modelo real usado.
> - `x-nan-auto-decision-id`: id para consultar el detalle.
> - `x-nan-auto-reason`: motivo corto (por ejemplo `coding_generate d=3 reasoning=on`).
> - `x-nan-auto-fallback`: modelos intentados antes del definitivo, si hubo.

> El campo `nan_auto` se elimina antes de reenviar a NaN.

> El cliente envía su clave de NaN en `Authorization`. El gateway la **reenvía** y nunca la guarda ni la escribe en logs.

De la sección 12:

| Cabecera `nan_auto` | Se valida con esquema y se elimina antes de reenviar |

## Objetivo

Que un cliente OpenAI real (Cursor, OpenCode, el SDK) apunte su URL base al gateway, mande `model: "nan-auto"` y reciba una respuesta correcta —con o sin streaming— elegida por el router, y que con cualquier otro modelo el gateway sea transparente.

## Problema y motivo

Es el punto donde el proyecto deja de ser biblioteca y pasa a ser producto. También es la superficie más peligrosa: maneja la clave del cliente, reescribe cuerpos ajenos y hace streaming. Un error acá filtra credenciales o rompe al cliente.

## Alcance

- Dentro: ruta `POST /v1/chat/completions`, decisión completa en el camino de enrutado, reenvío no-streaming, reenvío SSE, cabeceras `x-nan-auto-*`, eliminación de `nan_auto`, reenvío de la clave del cliente, passthrough cuando el modelo no es un alias.
- Fuera: fallback, circuit breaker y limitador de concurrencia (NAU-10), presupuesto de decisión con falla abierta (NAU-11), registro de decisiones (NAU-13), pasarela de `GET /v1/models` (ya existe como scaffold y la mejora NAU-03).

## Restricciones

- La clave del cliente **solo** viaja en la cabecera hacia NaN. No se guarda, no se loguea, no se devuelve, no aparece en mensajes de error. Un test con canario lo prueba.
- Si el modelo pedido no es un alias, el cuerpo se reenvía sin tocarlo, incluido `nan_auto` si viniera (no es nuestra petición).
- Un alias con `nan_auto` inválido responde `400` con el error del esquema, sin llamar a nadie.
- El comportamiento debe ser el de OpenAI: códigos de estado y forma del cuerpo de error compatibles.
- Sin streaming, el cuerpo de la respuesta se reenvía tal cual llegó.
- Con streaming, los bytes se reenvían a medida que llegan; no se acumula el stream entero.
- Un error **antes del primer byte** se propaga con su código; un corte **después** del primer byte no puede reintentarse (spec 5.5) y se propaga.

## Configuración de pruebas

- Modo TDD: activado. Runner `uv run pytest server/tests/test_proxy*.py`.
- Fuente: sección 15.3 del spec.

## Entrega

- Estrategia: ask-on-risk. Rama `feature/nau-07-proxy`.
- Líneas previstas: ~500.
- Verificación: **verificador independiente obligatorio** (ítem 2 de riesgo: manejo de la clave del cliente).

## Tareas

- [ ] NAU-07-T1 Esquema del campo `nan_auto` (perfil, `explain`, `allow_premium`) y `400` si es inválido. Aceptación: un `nan_auto` con perfil desconocido se rechaza sin llamar a NaN. Riesgo RDD: medio
- [ ] NAU-07-T2 Ruta `POST /v1/chat/completions` que decide y reenvía sin streaming. Aceptación: un cliente OpenAI recibe la respuesta del modelo elegido y las cabeceras `x-nan-auto-*`. Riesgo RDD: **alto**
- [ ] NAU-07-T3 Passthrough: modelo que no es alias → cuerpo intacto, sin decisión. Aceptación: la respuesta de NaN es byte a byte la del upstream. Riesgo RDD: **alto**
- [ ] NAU-07-T4 La clave del cliente se reenvía y nunca se guarda ni se loguea. Aceptación: test de canario en éxito, en error del upstream y en error de decisión. Riesgo RDD: **alto**
- [ ] NAU-08-T1 Streaming SSE de extremo a extremo con `StreamingResponse`. Aceptación: los eventos llegan y se reenvían; el cuerpo no se acumula. Riesgo RDD: **alto**
- [ ] NAU-08-T2 Cabeceras `x-nan-auto-model`, `-decision-id`, `-reason` en streaming y sin streaming. Aceptación: ambas rutas las devuelven. Riesgo RDD: medio
- [ ] NAU-08-T3 Error antes del primer byte vs corte después. Aceptación: dos tests, códigos distintos, y el segundo no reintenta. Riesgo RDD: **alto**

## Evidencia de verificación

| Tarea | Prueba | Resultado | Commit |
|---|---|---|---|
| — | pendiente | — | — |

## Revisión nativa (RDD)

| Tarea | Nivel | Resultado |
|---|---|---|
| T1-T3, T1-T3 (08) | alto | verificación independiente obligatoria |

## Progreso

Documento abierto.

## Siguiente paso

T1 y T2.
