# NAU-04: extractor de features deterministas

## Especificación (literal)

Del spec, sección 14:

> NAU-04 | Extractor de features | Casos de imagen, audio, tools, formato y tokens | Medio

De la sección 6.1:

| Feature | Origen |
|---|---|
| `has_image`, `has_audio` | Partes de contenido del mensaje (`image_url`, `input_audio`) |
| `est_tokens` | Estimación sobre todos los mensajes |
| `has_tools`, `tool_choice` | Campo `tools` de la petición |
| `response_format` | `text`, `json_object` o `json_schema` |
| `stream` | Campo `stream` |
| `turns`, `has_code_fences` | Recuento y heurística simple |
| `conversation_key` | Cabecera `x-conversation-id` o hash de system prompt + primer mensaje |

De la sección 3:

> Código determinista | Visión, audio, contexto necesario, herramientas, formato de salida, disponibilidad del modelo | Son hechos de la petición. No tiene sentido preguntarlos a un modelo

## Objetivo

Una función pura que, dado un cuerpo `chat/completions`, devuelva los hechos observables de la petición sin mirar semántica y **sin guardar el texto**.

## Problema y motivo

Es el primer paso del pipeline y el que alimenta el filtro de capacidades. Si el extractor se equivoca, un prompt con audio llega a un modelo de solo texto (fallo visible) o se manda el prompt entero a Clef (fallo de privacidad y de latencia).

## Alcance

- Dentro: modelos Pydantic mínimos del cuerpo OpenAI, extractor puro, estimación de tokens, clave de conversación, tests de cada caso.
- Fuera: el filtro de capacidades y la política (NAU-06), el proxy (NAU-07), la construcción del `state` para Clef (ya existe en `clef_client/questions.py`).

## Restricciones

- El extractor no guarda ni registra contenido: devuelve solo hechos y números.
- Acepta cuerpos con campos desconocidos sin fallar: los clientes OpenAI mandan de todo.
- `content` puede ser string o lista de partes; ambas formas cuentan.
- Un cuerpo inválido no debe reventar con `AttributeError` ni `KeyError`: error explícito.
- Nada de red.

## Configuración de pruebas

- Modo TDD: activado. Runner `uv run pytest server/tests/test_features.py`.
- Fuente: sección 15.3 del spec.

## Entrega

- Estrategia: ask-on-risk. Rama `feature/nau-04-features`.
- Líneas previstas: ~350.

## Tareas

- [ ] NAU-04-T1 Modelos Pydantic del cuerpo (`ChatRequest`, mensajes, partes) tolerantes a campos extra. Aceptación: un cuerpo real de Cursor con campos desconocidos se valida. Riesgo RDD: medio
- [ ] NAU-04-T2 `has_image` / `has_audio` sobre partes `image_url` e `input_audio`, con `content` string y lista. Aceptación: un mensaje con imagen lo marca; uno de solo texto no. Riesgo RDD: medio
- [ ] NAU-04-T3 `est_tokens` sobre todos los mensajes más las partes no textuales. Aceptación: un mensaje de 4000 caracteres estima ~1000 tokens. Riesgo RDD: bajo
- [ ] NAU-04-T4 `has_tools`, `tool_choice`, `response_format`, `stream`. Aceptación: un cuerpo con `tools` y `response_format: json_schema` se refleja tal cual. Riesgo RDD: medio
- [ ] NAU-04-T5 `turns`, `has_code_fences`. Aceptación: tres mensajes dan tres turnos; un fence los detecta. Riesgo RDD: bajo
- [ ] NAU-04-T6 `conversation_key`: cabecera `x-conversation-id` si existe, si no hash estable de system prompt + primer mensaje. Aceptación: dos peticiones de la misma conversación dan la misma clave, y sin cabecera también. Riesgo RDD: medio

## Evidencia de verificación

| Tarea | Prueba | Resultado | Commit |
|---|---|---|---|
| — | pendiente | — | — |

## Revisión nativa (RDD)

| Tarea | Nivel | Resultado |
|---|---|---|
| T1-T6 | medio | pendiente de assess al cerrar el slice |

## Progreso

Documento abierto.

## Siguiente paso

T1 y T2.
