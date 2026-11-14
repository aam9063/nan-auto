# NAU-14/15: matriz de calidad y evaluación offline

## Especificación (literal)

Del spec, sección 14:

> NAU-14 | Benchmark: dataset y matriz de calidad | Matriz completa, cacheada y reproducible | Medio
> NAU-15 | Harness de evaluación offline y baselines | `uv run python eval/run.py` genera tabla y gráfica de Pareto | Medio

De la sección 11.1:

> 2. **Matriz de calidad** (`eval/data/quality.parquet` o SQLite): para cada prompt, la respuesta de **cada modelo** del catálogo, con calidad puntuada, latencia y tokens. Se genera **una vez**.

De la sección 11.2:

> Con la matriz de calidad ya calculada, **cualquier política se evalúa sin nuevas llamadas**: para cada prompt se consulta qué modelo habría elegido y se lee su calidad y latencia de la matriz.

De la sección 11.6:

> Generar la matriz implica del orden de `prompts x modelos` llamadas… Hay que respetar los límites por clave de NaN con un limitador de concurrencia y reintentos con backoff. **Se lanza en la Fase 0 y se cachea en disco.**

De la sección 11.5:

> **Gráfica principal:** latencia en el eje X y calidad en el Y. Cada modelo único es un punto. El router debería quedar **por encima de la línea** que une a los modelos únicos.

## Objetivo

Medir, con llamadas reales y una sola vez, la calidad y la latencia de cada modelo del catálogo
sobre el mismo conjunto de prompts; guardarlo en disco; y evaluar **cualquier política sin nuevas
llamadas**.

## Problema y motivo

Las constantes de la política están marcadas `[HIPÓTESIS]` en el propio código. Sin esta matriz, la
frase "el router queda por encima de la línea" es una afirmación sin respaldo, y la pantalla de
evaluación del dashboard está vacía — hoy lo dice honestamente, pero no siempre puede estarlo.

## Alcance

- Dentro: dataset de prompts con etiquetas, generador de la matriz cacheado y reanudable, juez con
  rúbrica para la calidad, y `eval/run.py` que produce tabla de baselines y datos del Pareto.
- Fuera: fine-tuning, revisión humana del 10% (se documenta como limitación), evaluación de audio.

## Restricciones

- **Cada llamada se cachea por (prompt_id, modelo)** en SQLite: relanzar no repite trabajo ni gasta
  cuota.
- Muestra honesta: si se ejecuta con N prompts, el informe dice N y qué implica. Nunca se presenta
  una muestra como si fuera el dataset completo.
- Limitador de concurrencia y reintento con backoff: la clave tiene límites publicados.
- El juez usa la misma API y su propio presupuesto; su sesgo de familia se documenta.
- `eval/run.py` **no llama a la red si la matriz ya está completa**: la evaluación de políticas es
  aritmética sobre el cache.

## Configuración de pruebas

- Modo TDD: activado para el cálculo y el cacheo. Runner `uv run pytest server/tests/test_eval_*.py`.
  La generación real de la matriz es una excepción documentada: es una operación de red larga, no
  un test.

## Entrega

- Estrategia: ask-on-risk. Rama `feature/nau-14-eval`.
- Líneas previstas: ~500.

## Tareas

- [x] NAU-14-T1 Dataset de intenciones etiquetado, pequeño y honesto (≥ 20 prompts, es/en, cortos y largos, con y sin código). Aceptación: cada línea trae `id`, `prompt`, `task`, `difficulty`. Riesgo RDD: bajo
- [x] NAU-14-T2 Generador de la matriz con cache SQLite por (prompt, modelo) y reanudación. Aceptación: dos ejecuciones seguidas no repiten llamadas. Riesgo RDD: medio
- [x] NAU-14-T3 Limitador y backoff en el generador. Aceptación: nunca supera el límite configurado. Riesgo RDD: **alto**
- [x] NAU-14-T4 Juez de calidad con rúbrica y orden de respuestas estable. Aceptación: puntuación reproducible sobre una respuesta dada. Riesgo RDD: medio
- [x] NAU-15-T1 `eval/run.py`: baselines R0-R3 sobre la matriz, sin llamadas. Aceptación: tabla con calidad media, latencia media y cobertura por política. Riesgo RDD: medio
- [x] NAU-15-T2 Datos del Pareto y de la retención de calidad. Aceptación: un punto por modelo único y uno por política. Riesgo RDD: bajo

## Evidencia de verificación

| Tarea | Prueba | Resultado | Commit |
|---|---|---|---|
| T1 | `test_intents_dataset_is_labeled_and_complete` | pass | worktree (rama `feature/nau-14-eval`, sin commit por instrucción) |
| T2 | `test_cache_roundtrip_stores_every_column`, `test_second_run_skips_every_cached_pair`, `test_resume_retries_pairs_that_errored_but_skips_answered_ones` | pass | worktree |
| T3 | `test_run_matrix_never_exceeds_the_concurrency_limit`, `test_rate_limited_calls_back_off_and_are_retried`, `test_persistent_failure_is_recorded_not_raised` | pass | worktree |
| T4 | `test_parse_score_accepts_only_the_documented_scale`, `test_the_rubric_is_a_deterministic_documented_scale`, `test_score_pending_writes_raw_reply_and_score_and_never_repeats` | pass | worktree |
| 15-T1 | `test_r0_*`, `test_r1_*`, `test_r2_*`, `test_r3_*`, `test_run_main_emits_json_and_a_readable_table`, `test_dry_run_prints_the_plan_and_costs_nothing` | pass | worktree |
| 15-T2 | `test_pareto_has_one_point_per_model_and_per_policy` | pass | worktree |
| Suite completa | `uv run pytest` (server) | 330 passed; ruff y pyright limpios | — |
| Smoke offline | `uv run --project server python eval/run.py` | corre sin red; 28 prompts; tabla honesta con matriz vacía | — |

## Revisión nativa (RDD)

| Tarea | Nivel | Resultado |
|---|---|---|
| T1-T4, T1-T2 | medio (T3 alto) | verificador recomendado para T3 (pendiente: decisión del humano) |

## Progreso

Implementación completa en el worktree de `feature/nau-14-eval` **sin commit** (instrucción explícita
de la delegación). Matriz real medida: **307/308 celdas** (solo `en-016`×`qwen3.6` sin medir, ni
siquiera con 16384 tokens), 307 respuestas juzgadas con glm5.3, reporte offline con la matriz
completa: `eval/data/report.json` + tabla legible.

Resultado honesto del Pareto: el router (R3, 3.57 de calidad a 13.4 s) queda **por debajo de la
frontera de modelos únicos** (gemma4 3.93 a 12.9 s; deepseek-v4-flash 3.68 a 9.8 s). Las constantes
de la política v1 estaban marcadas `[HIPÓTESIS]`; el dato ahora respalda afinarlas (NAU siguó).

## Siguiente paso

Pendiente de decisión del humano: (1) commit de la entrega en `feature/nau-14-eval`; (2) afinar la
política v1 con la matriz real (los baselines muestran que siempre deepseek-v4-flash domina a
R1/R2 en latencia sin sacrificar calidad); (3) verificador independiente para T3 (riesgo alto).

## Log

- **L1** (delegación, 2026-11-14 16:04): «Implement the offline evaluation harness in
  C:/Users/alber/orca/nan-auto. Verify `git branch --show-current` returns feature/nau-14-eval and
  report it. Do not commit.» — Allowed edit surfaces: `eval/…`.
- **L2** — Implementación completa del harness (T1–T4, NAU-15-T1/T2): 25 pruebas de eval, suite 330
  verde, worktree sin commit.
- **L3** (2026-11-14 18:5x, decisión del humano): «tengo billones de tokens, si es solo 1 millon no
  pasa nada.» → autorizada la toda la generación y el judge reales.
- **L4** — Primera pasada con cap 1024: 260/308; kimi-k3.1-pro 400 sistemático (rechaza cualquier
  `temperature` explícita), qwen3.6 quemaba el cap pensando (`content: None`). Fix: reintento sin
  temperatura (`+test`), cap uniforme 4096 y timeout 240 s, matrix regenerada de cero (base vieja
  archivada). Firma estricta: ruff/pyright en verde.
- **L5** — Segunda pasada: 303/308. Flags `--max-tokens/--timeout`; 4 celdas duras recuperadas con
  8192 tokens/480 s; `en-016` en qwen3.6 sigue sin respuesta ni con 16384 tokens → matriz 307/308,
  `complete: false` honesto en el reporte. Documentación en `eval/README.md`.
- **L6** — Juez finalizado: 307/307 puntuados (distribución sesgada a 4: 261 cuatro, 24 cero, 13
  tres, 9 dos; respuestas crudas cacheadas para auditar). Reporte offline final en
  `eval/data/report.json`: R0=R2 mejor calidad-latencia (deepseek-v4-flash 3.68/9.8 s), R3 router
  3.57/13.4 s — el router queda por debajo de la frontera; afinamiento de política = paso sugido.
