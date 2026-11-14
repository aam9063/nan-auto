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

- [x] NAU-04-T1 Modelos Pydantic del cuerpo (`ChatRequest`, mensajes, partes) tolerantes a campos extra. Aceptación: un cuerpo real de Cursor con campos desconocidos se valida. Riesgo RDD: medio
- [x] NAU-04-T2 `has_image` / `has_audio` sobre partes `image_url` e `input_audio`, con `content` string y lista. Aceptación: un mensaje con imagen lo marca; uno de solo texto no. Riesgo RDD: medio
- [x] NAU-04-T3 `est_tokens` sobre todos los mensajes más las partes no textuales. Aceptación: un mensaje de 4000 caracteres estima ~1000 tokens. Riesgo RDD: bajo
- [x] NAU-04-T4 `has_tools`, `tool_choice`, `response_format`, `stream`. Aceptación: un cuerpo con `tools` y `response_format: json_schema` se refleja tal cual. Riesgo RDD: medio
- [x] NAU-04-T5 `turns`, `has_code_fences`. Aceptación: tres mensajes dan tres turnos; un fence los detecta. Riesgo RDD: bajo
- [x] NAU-04-T6 `conversation_key`: cabecera `x-conversation-id` si existe, si no hash estable de system prompt + primer mensaje. Aceptación: dos peticiones de la misma conversación dan la misma clave, y sin cabecera también. Riesgo RDD: medio

## Evidencia de verificación

| Tarea | Prueba | Resultado | Commit |
|---|---|---|---|
| T1 | `uv run pytest tests/test_features.py` | RED de colección (`No module named 'nan_auto.router_core.features'`) | `feat(router_core)` |
| T2 | idem | RED: `has_image=False` / `has_audio=False` en 4 casos | `feat(router_core)` |
| T3 | idem | RED: `est_tokens=0` en 5 casos | `feat(router_core)` |
| T4 | idem | RED: `tool_choice=None`, `has_tools=False`, `response_format='text'` | `feat(router_core)` |
| T5 | idem | RED: `turns=0`, `has_code_fences=False` | `feat(router_core)` |
| T6 | idem | RED: `conversation_key=''` en 4 casos | `feat(router_core)` |
| suite | `uv run pytest` | 97 → **131 passed** | `feat(router_core)` |
| estático | `ruff check .` + `pyright` | 0 errores, 0 warnings | `feat(router_core)` |

Sonda propia de privacidad (canario `SECRETO-DE-USUARIO-9911` en system, user, imagen y audio):

```
RequestFeatures(has_image=True, has_audio=True, est_tokens=192, has_tools=True,
                tool_choice=None, response_format='json_schema', stream=True,
                turns=3, has_code_fences=True,
                conversation_key='75fa538970cf197517cf5b228ffb90c2')
canary leaked en el repr: False
```

Sonda propia de la clave de conversación:

| Caso | Resultado |
|---|---|
| misma conversación con más turnos | igual |
| primer mensaje distinto | distinta |
| system distinto | distinta |
| cabecera `x-conversation-id` presente | `c-1` (manda la cabecera) |

### Decisiones que el worker dejó al padre

- `response_format` ausente → `"text"` (default de OpenAI; la tabla del spec solo lista los tres valores).
- Cada parte no textual suma un piso fijo de ~85 tokens; el spec no da número.
- `tool_choice` se refleja tal cual, sin normalizar.

## Revisión nativa (RDD)

| Tarea | Nivel | Resultado |
|---|---|---|
| T1-T6 | medio | pendiente de assess al cerrar el slice |

## Progreso

NAU-04 implementada y verificada. Pendiente el merge y el assess del slice.

## Siguiente paso

NAU-06: filtro de capacidades y política v1, que consume estas features.
