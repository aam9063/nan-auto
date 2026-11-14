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

- [ ] NAU-03-T1 `ModelInfo` y cliente de catálogo contra `/v1/models` (con respx y con catálogo inyectado). Aceptación: 200 parsea 18 modelos; un campo opcional ausente no rompe. Riesgo RDD: medio
- [ ] NAU-03-T2 Fusión con `config/models.yaml`: la ficha aporta modalidades, tools, razonamiento, premium y escalón. Aceptación: un modelo de la ficha conserva sus metadatos. Riesgo RDD: medio
- [ ] NAU-03-T3 Modelo desconocido = generalista, nunca descartado. Aceptación: un id inventado aparece con defaults y sin escalón. Riesgo RDD: medio
- [ ] NAU-03-T4 Exclusión de no-chat por ficha, no por `mode`. Aceptación: `flux-2-klein` y `qwen-image-2.1` quedan fuera pese a anunciarse como chat, y un `mode: embedding` también. Riesgo RDD: medio
- [ ] NAU-03-T5 Salud y cuota por modelo: `mark_failure`, `mark_quota_exhausted`, `is_available`. Aceptación: un 402 lo saca hasta que se renueve; un fallo cuenta. Riesgo RDD: medio
- [ ] NAU-03-T6 `GET /v1/registry` con estado por modelo, sin clave ni datos de petición. Aceptación: el endpoint responde el inventario fusionado. Riesgo RDD: bajo

## Evidencia de verificación

| Tarea | Prueba | Resultado | Commit |
|---|---|---|---|
| — | pendiente | — | — |

## Revisión nativa (RDD)

| Tarea | Nivel | Resultado |
|---|---|---|
| T1-T6 | medio | pendiente de assess al cerrar el slice |

## Progreso

Documento abierto. Sin implementación.

## Siguiente paso

T1 y T2.
