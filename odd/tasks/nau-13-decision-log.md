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

- [ ] NAU-13-T1 Esquema y migración inicial (tablas del spec 9 con sus claves foráneas). Aceptación: crear la base en un fichero temporal funciona y es idempotente. Riesgo RDD: medio
- [ ] NAU-13-T2 `record_decision()` con respuestas de Clef e intentos en una transacción. Aceptación: una decisión con dos intentos deja 1 + N + M filas. Riesgo RDD: medio
- [ ] NAU-13-T3 `list_decisions()` y `get_decision()` para el dashboard. Aceptación: devuelven lo escrito, ordenado y con sus respuestas. Riesgo RDD: bajo
- [ ] NAU-13-T4 La API no admite contenido: sin parámetro de texto libre. Aceptación: un test que recorre las firmas y comprueba que no existe. Riesgo RDD: **alto**
- [ ] NAU-13-T5 Canario: prompt y clave no aparecen en ninguna columna de ninguna tabla. Aceptación: recorrido completo de `sqlite_master` + filas. Riesgo RDD: **alto**

## Evidencia de verificación

| Tarea | Prueba | Resultado | Commit |
|---|---|---|---|
| — | pendiente | — | — |

## Revisión nativa (RDD)

| Tarea | Nivel | Resultado |
|---|---|---|
| T1-T5 | alto | verificador independiente obligatorio |

## Progreso

Documento abierto.

## Siguiente paso

T1 a T5.
