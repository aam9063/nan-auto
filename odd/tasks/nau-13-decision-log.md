# NAU-13: registro de decisiones sin contenido

## Especificación (literal)

Del spec, sección 14:

> NAU-13 | Log de decisiones sin contenido + SSE | Ningún prompt ni clave en la base de datos (test) | **Alto**

De la sección 9:

> Por defecto **no se guarda el contenido de los prompts** ni de las respuestas: solo características, probabilidades, tiempos y tokens (el contenido solo se guarda en el dataset de evaluación, que contiene prompts sintéticos o públicos). Es coherente con la propuesta de valor de NaN: sin registro de prompts.

Modelo de datos del spec 9: `REQUEST_LOG` (id, conversation_hash, profile, chosen_model,
reasoning_level, decision_source, clef_latency_ms, ttft_ms, total_ms, prompt_tokens,
completion_tokens, created_at), `DECISION_ANSWER` (request_id, question_id, label, value,
confidence), `ATTEMPT` (request_id, model, outcome, http_status, latency_ms).

De la sección 12:

> Contenido | No se guarda por defecto. Solo features, probabilidades y métricas. `LOG_CONTENT=true` solo para depurar en local

## Objetivo

Una capa de persistencia SQLite que guarde **qué decidió el router y por qué**, con las
probabilidades de Clef y los tiempos, y que sea estructuralmente incapaz de guardar el texto
del usuario o la clave.

## Problema y motivo

El dashboard, el feed en vivo y la evaluación offline leen de acá. A la vez, es la superficie
donde un descuido viola la promesa central del producto ("sin registro de prompts"). El riesgo
no es un bug visible: es guardar de más y no notarlo nunca.

## Alcance

- Dentro: esquema SQLite, repositorio asíncrono (`aiosqlite`), inserción de una decisión con sus
  respuestas y intentos, lectura para el dashboard (`GET /v1/decisions` y por id), y una prueba
  canario de que ni el prompt ni la clave pueden entrar.
- Fuera: el SSE del feed en vivo y el cableado en el proxy (se hacen cuando el proxy esté
  fusionado), la evaluación offline (NAU-15), `LOG_CONTENT` (fuera de alcance por ahora: el
  proyecto no lo necesita para la demo y añade una vía de fuga).

## Restricciones

- **La API del repositorio no acepta texto libre.** No hay parámetro para el prompt, ni para el
  cuerpo, ni para la clave. Que no se pueda pasar no depende de que alguien se acuerde.
- Solo entran identificadores, números, enumerados y las respuestas de Clef (etiqueta,
  valor, confianza) que ya son hechos.
- `conversation_hash` es el hash de conversación, nunca la clave de conversación cruda si esa
  clave podría contener texto del cliente (la cabecera `x-conversation-id` la elige el cliente).
- Un test canario recorre todas las columnas de todas las tablas buscando el prompt y la clave.
- Escrituras y lecturas asíncronas; sin bloqueo del event loop.

## Configuración de pruebas

- Modo TDD: activado. Runner `uv run pytest server/tests/test_decision_log.py`.
- Fuente: sección 15.3 del spec.

## Entrega

- Estrategia: ask-on-risk. Rama `feature/nau-13-log`.
- Líneas previstas: ~350.
- Verificación: verificador independiente (ítem 2: es la superficie de privacidad).

## Tareas

- [x] NAU-13-T1 Esquema y migración inicial (tablas del spec 9 con sus claves foráneas). Aceptación: crear la base en un fichero temporal funciona y es idempotente. Riesgo RDD: medio
- [x] NAU-13-T2 `record_decision()` con respuestas de Clef e intentos en una transacción. Aceptación: una decisión con dos intentos deja 1 + N + M filas. Riesgo RDD: medio
- [x] NAU-13-T3 `list_decisions()` y `get_decision()` para el dashboard. Aceptación: devuelven lo escrito, ordenado y con sus respuestas. Riesgo RDD: bajo
- [x] NAU-13-T4 La API no admite contenido: sin parámetro de texto libre. Aceptación: un test que recorre las firmas y comprueba que no existe. Riesgo RDD: **alto**
- [ ] NAU-13-T6 Cablear el log en el proxy. Aceptación: una petición deja una fila con modelo, nivel y tiempos, y el stream no se bloquea. Riesgo RDD: **alto**
- [x] NAU-13-T5 Canario: prompt y clave no aparecen en ninguna columna de ninguna tabla. Aceptación: recorrido completo de `sqlite_master` + filas. Riesgo RDD: **alto**

## Evidencia de verificación

| Tarea | Prueba | Resultado | Commit |
|---|---|---|---|
| T1-T5 | `uv run pytest tests/test_decision_log.py` | RED de colección (`ImportError: log_store`) → 15 passed | `feat(gateway)` |
| suite | `uv run pytest` | 231 → **246 passed** | `feat(gateway)` |
| estático | `ruff check .` + `pyright` | 0 errores, 0 warnings | `feat(gateway)` |
| canario propio | sonda del padre sobre las 3 tablas | `columnas con el prompt o la clave: NINGUNA` | `feat(gateway)` |

### API pública expuesta

`DecisionLogStore(path)` con `open()`, `record_decision(record, *, answers, attempts)`,
`list_decisions(limit)` y `get_decision(request_id)`; más `DecisionRecord`, `ClefAnswer`,
`Attempt`, `StoredDecision`, `DecisionLogEntry` y `DecisionLogError`. **Ningún parámetro de texto
libre.**

### Ambigüedades del modelo de datos, resueltas hacia el spec 9

- La lista de tareas no incluía `clef_timeout`; el spec 9 sí. Se incluye.
- El spec no fija vocabularios: `decision_source` y `outcome` son etiquetas cortas
  (`clef`/`fallback`, `success`/`http_error`), `reasoning_level` es un `Literal` con los cuatro
  niveles de la política. El cableado en el proxy debe usar estas mismas etiquetas.
- `ATTEMPT` y `DECISION_ANSWER` se leen por orden de inserción (rowid), porque el spec no define
  columna de orden.

### Incidente de topología

El worker implementó sobre el árbol que tenía (`dev`), no sobre `feature/nau-13-log`, y lo declaró
como riesgo en su informe. Los dos ficheros eran nuevos, así que viajaron con el cambio de rama:
se ejecutó la suite en la rama correcta (222 = 207 sin proxy + 15 nuevos) y recién entonces se
fusionó.

## Revisión nativa (RDD)

| Tarea | Nivel | Resultado |
|---|---|---|
| T1-T5 | alto | verificador independiente obligatorio |

## Progreso

Capa de datos fusionada. Falta el cableado: el proxy todavía no escribe en el log.

## Siguiente paso

T6: cablear el log en `gateway/proxy.py` (una escritura por petición, sin bloquear el stream) y
el SSE del feed en vivo.
