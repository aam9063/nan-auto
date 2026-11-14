# NAU-16a: endpoints de lectura y SSE para el dashboard

## Especificación (literal)

Del spec, sección 8:

| Método y ruta | Descripción |
|---|---|
| `GET /v1/decisions` y `GET /v1/decisions/:id` | Historial y detalle con probabilidades (para el dashboard) |
| `POST /v1/playground/decide` | Solo decide, no llama al modelo. Devuelve la elección y las probabilidades |
| `GET /v1/stream` | SSE con las decisiones en vivo |
| `GET /v1/registry` | Estado de los modelos: disponibles, salud, cuota |

De la sección 10 (dashboard):

> **Playground**: cajón de texto y adjuntos. Muestra las probabilidades de Clef por pregunta, los
> candidatos descartados con el motivo, la puntuación y el modelo elegido.

> **Feed en vivo**: Peticiones en tiempo real con modelo, razonamiento, TTFT, total, fallbacks

De la sección 12:

> Claves | Reenvío sin almacenamiento, redacción de cabeceras en logs, nunca en el dashboard

## Objetivo

Exponer lo que el dashboard necesita leer: el historial de decisiones con sus probabilidades, una
decisión sin llamar al modelo, y un canal SSE con las decisiones en vivo.

## Problema y motivo

El dashboard es la pantalla que explica el producto: sin estas rutas, el playground no puede
mostrar "por qué este modelo" y el feed en vivo no tiene de dónde leer.

## Alcance

- Dentro: `GET /v1/decisions`, `GET /v1/decisions/{id}`, `POST /v1/playground/decide`,
  `GET /v1/stream`, y un bus de eventos en proceso al que el proxy publica cada decisión.
- Fuera: la UI (unidad siguiente), la evaluación (NAU-15).

## Restricciones

- **Nada de contenido ni de claves**: las rutas leen del log que ya garantiza esa invariante por
  construcción. El playground devuelve probabilidades y motivos, nunca el prompt.
- `POST /v1/playground/decide` **no llama a ningún modelo**: solo features, Clef, filtro y
  política. Es la pantalla de "por qué".
- `GET /v1/stream` no puede bloquear ni acumular: si un cliente es lento o se va, se descarta su
  suscripción sin afectar al resto.
- Un cliente SSE que se desconecta no debe dejar la tarea colgada.
- `GET /v1/decisions/{id}` de un id inexistente es un `404`, no un `500`.
- Sin log configurado, `/v1/decisions` responde una lista vacía, no un error.

## Configuración de pruebas

- Modo TDD: activado. Runner `uv run pytest server/tests/test_decisions_api.py server/tests/test_stream_sse.py`.

## Entrega

- Estrategia: ask-on-risk. Rama `feature/nau-16-read-api`.
- Líneas previstas: ~400.

## Tareas

- [ ] NAU-16-T1 `GET /v1/decisions` con límite y `GET /v1/decisions/{id}` con 404. Aceptación: lista ordenada y detalle con sus respuestas. Riesgo RDD: medio
- [ ] NAU-16-T2 `POST /v1/playground/decide`: decide sin llamar al modelo. Aceptación: la respuesta trae modelo, nivel, motivo, probabilidades y descartes; **cero** llamadas al upstream. Riesgo RDD: medio
- [ ] NAU-16-T3 Bus de eventos y `GET /v1/stream` en SSE. Aceptación: una petición enrutada emite un evento y el suscriptor lo recibe. Riesgo RDD: **alto**
- [ ] NAU-16-T4 Un suscriptor lento o desconectado no afecta a los demás ni deja tareas colgadas. Aceptación: dos suscriptores, uno se va, el otro sigue recibiendo. Riesgo RDD: **alto**
- [ ] NAU-16-T5 Ninguna de las rutas nuevas expone contenido ni claves. Aceptación: canario en el prompt y en la cabecera, recorrido de todas las respuestas. Riesgo RDD: **alto**

## Evidencia de verificación

| Tarea | Prueba | Resultado | Commit |
|---|---|---|---|
| — | pendiente | — | — |

## Revisión nativa (RDD)

| Tarea | Nivel | Resultado |
|---|---|---|
| T1-T5 | alto (T3-T5) | verificador independiente recomendado |

## Progreso

Documento abierto.

## Siguiente paso

T1 y T2.
