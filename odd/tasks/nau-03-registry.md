# NAU-03: registro de modelos desde `/v1/models` + `models.yaml`

## Especificación (literal)

Del spec, sección 14:

> NAU-03 | Registro de modelos desde `/v1/models` + `models.yaml` | Un modelo nuevo aparece sin tocar código | Medio

De la sección 4:

> El catálogo cambia (la comunidad vota modelos cada trimestre), así que **el registro se construye al arrancar con `GET /v1/models`** y la ficha de capacidades del YAML solo aporta metadatos. Un modelo desconocido se trata como "generalista" por defecto.

De la sección 5.2:

> Registro de modelos | Carga `/v1/models`, fusiona con `models.yaml`, mantiene salud y cuota de cada modelo

## Objetivo

Un registro que, al arrancar, descubre el catálogo real y lo fusiona con la ficha local, de modo que un modelo nuevo funcione sin tocar código y un modelo desconocido no rompa nada.

## Problema y motivo

Ya está demostrado con datos reales (ronda de NAU-02) que la ficha del spec está desactualizada: el catálogo vivo tiene 18 modelos, de los que 12 son de chat, y **`flux-2-klein` y `qwen-image-2.1` se anuncian con `mode: chat` siendo generadores de imagen**. Si el registro confía en `mode`, un prompt de texto puede acabar en un generador de imágenes.

## Catálogo observado (2026-11-14)

`GET /v1/models` → `{"object":"list","data":[{"id","created","object","owned_by","mode","max_input_tokens","max_output_tokens"}]}`.

- Campos presentes: `id`, `created`, `object`, `owned_by`, `mode`, y opcionales `max_input_tokens` / `max_output_tokens`.
- `mode` ∈ `chat`, `embedding`, `rerank`, `audio_speech`, `audio_transcription`.
- No informa modalidades de entrada, soporte de tools, forma de apagar el razonamiento, ni si es premium: eso solo está en `config/models.yaml`.

## Alcance

- Dentro: cliente de catálogo, fusión con la ficha, defaults para desconocidos, exclusión de no-chat por ficha, estado de salud y cuota por modelo, refresco con TTL, endpoint `GET /v1/registry`.
- Fuera: puntuación y política (NAU-06), circuit breaker con fallback (NAU-10), consumo del registro en el proxy (NAU-07).

## Restricciones

- El registro no puede fiarse de `mode` para decidir si un modelo sirve texto: la ficha local manda.
- Un modelo del catálogo que no esté en la ficha se trata como generalista, jamás se descarta.
- La clave no aparece en logs ni en la respuesta de `/v1/registry`.
- `mock` debe funcionar sin red: el registro acepta un catálogo inyectado.

## Configuración de pruebas

- Modo TDD: activado. Runner `uv run pytest server/tests/test_registry_*`.
- Fuente: sección 15.3 del spec.

## Entrega

- Estrategia: ask-on-risk. Rama `feature/nau-03-registry`.
- Líneas previstas: ~420.

## Tareas

- [x] NAU-03-T1 `ModelInfo` y cliente de catálogo contra `/v1/models` (con respx y con catálogo inyectado). Aceptación: 200 parsea 18 modelos; un campo opcional ausente no rompe. Riesgo RDD: medio
- [x] NAU-03-T2 Fusión con `config/models.yaml`: la ficha aporta modalidades, tools, razonamiento, premium y escalón. Aceptación: un modelo de la ficha conserva sus metadatos. Riesgo RDD: medio
- [x] NAU-03-T3 Modelo desconocido = generalista, nunca descartado. Aceptación: un id inventado aparece con defaults y sin escalón. Riesgo RDD: medio
- [x] NAU-03-T4 Exclusión de no-chat por ficha, no por `mode`. Aceptación: `flux-2-klein` y `qwen-image-2.1` quedan fuera pese a anunciarse como chat, y un `mode: embedding` también. Riesgo RDD: medio
- [x] NAU-03-T5 Salud y cuota por modelo: `mark_failure`, `mark_quota_exhausted`, `is_available`. Aceptación: un 402 lo saca hasta que se renueve; un fallo cuenta. Riesgo RDD: medio
- [x] NAU-03-T6 `GET /v1/registry` con estado por modelo, sin clave ni datos de petición. Aceptación: el endpoint responde el inventario fusionado. Riesgo RDD: bajo
- [x] NAU-03-T7 `excluded_from_policy` de la ficha se refleja como `ModelInfo.excluded` sin sacar el modelo del inventario. Aceptación: `minimax-h3` aparece, marcado y premium. Riesgo RDD: bajo

## Evidencia de verificación

| Tarea | Prueba | Resultado | Commit |
|---|---|---|---|
| T1-T4 | `uv run pytest tests/test_registry_catalog.py` | RED de colección (`No module named 'nan_auto.registry.catalog'`) → verde | `feat(registry)` |
| T5 | `uv run pytest tests/test_registry_state.py` | verde | `feat(registry)` |
| T6 | `uv run pytest tests/test_registry_endpoint.py` | verde, con canario de clave y URL | `feat(registry)` |
| T7 | test de `excluded_from_policy` | añadido por el padre tras el spot-check | `feat(registry)` |
| suite | `uv run pytest` | 51 → 97 passed | `feat(registry)` |
| estático | `ruff check .` + `pyright` | 0 errores, 0 warnings | `feat(registry)` |
| **catálogo real** | `CatalogClient` contra `api.nan.builders/v1/models` | 18 vivos → **12 servibles**; excluidos exactamente `flux-2-klein, kokoro, qwen-image-2.1, qwen3-embedding, rerank, whisper` | — |

Inventario real fusionado (extracto):

```
t1_fast:      gemma4 (text,image), glm5.3-flash, qwen3.8-flash
t2_generalist: deepseek-v4-flash, qwen3.6
t3_max:       glm5.3 (premium), mimo-v2.6-flash (text,image,audio)
unclassified: deepseek-v4-pro, hy4, kimi-k3.1-pro, minimax-h3 (premium, excluido de política), qwen3.8-max
```

### Gotcha de YAML encontrado por el worker

YAML 1.1 parsea las claves desnudas `on:` y `off:` como booleanos, así que
`reasoning.on` de la ficha llegaba vacío y el adaptador de razonamiento habría
perdido el interruptor en silencio. `_reasoning_branch` ahora consulta la clave
como string y como booleano.

### Desviación aceptada

El spec dice "se construye al arrancar". Se implementó **refresco perezoso con TTL**
(300 s) en la primera petición a `/v1/registry`, no en el arranque, para que el
scaffold y el modo `mock` no hagan red al importar. El endpoint falla abierto al
inventario de la ficha si el catálogo no responde. Si se quiere refresco en el
`lifespan`, es un cambio de tres líneas en `gateway/app.py`.

## Revisión nativa (RDD)

| Tarea | Nivel | Resultado |
|---|---|---|
| T1-T6 | medio | pendiente de assess al cerrar el slice |

## Progreso

NAU-03 implementada, verificada contra el catálogo real y fusionada a `dev`.

## Siguiente paso

NAU-04: extractor de features (imagen, audio, tools, formato, tokens).
