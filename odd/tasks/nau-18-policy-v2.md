# NAU-18: política v2 con latencias medidas

## Especificación (intento del usuario, literal)

> «dale» (2026-11-14, tras proponer: commit + verificador T3 + afinamiento de la política v1 con
> la matriz real).
> Elección explícita de objetivo: «Latencia primero, piso de calidad» — "Menor latencia entre los
> modelos con calidad medida ≥ umbral. La simulación da 10,3 s con q=4,0".

## Objetivo

Reemplazar en el router las constantes `[HIPÓTESIS]` de latencia y las filas del spec 7.2 que la
matriz real (NAU-14, `eval/data/quality.sqlite3`, 2026-11-14) desmiente, con regla:
**menor latencia medida entre los modelos con calidad media ≥ 3,8 para la banda de dificultad.**

## Datos medidos (fuente de verdad)

| modelo | tier tarjeta | q media | latencia media |
|---|---|---|---|
| deepseek-v4-flash | t2_generalist | 3.68 (1 cero) | 9.8 s |
| gemma4 | t1_fast | 3.93 | 12.9 s |
| qwen3.6 | t2_generalist | 3.81 | 15.6 s |
| glm5.3 | t3_max | 3.68 (2 ceros) | 15.9 s |
| deepseek-v4-pro | unclassified | 3.07 (6 ceros) | 17.0 s |
| hy4 | unclassified | 3.04 (6 ceros) | 20.5 s |
| glm5.3-flash | t1_fast | 3.29 (4 ceros) | 21.3 s |
| kimi-k3.1-pro | unclassified | 3.50 (3 ceros) | 23.9 s |
| qwen3.8-flash | t1_fast | 3.93 | 25.2 s |
| mimo-v2.6-flash | t3_max | 3.61 | 36.0 s |
| qwen3.8-max | unclassified | 3.93 | 51.8 s |

## Alcance

- Dentro: `_LATENCY_BY_TIER` → latencias medidas por modelo (fallback al prior por tier sin
  medida); filas de dificultad de `chat_casual`/`factual_qa` y del default con los ganadores
  medidos; verificación offline con `eval/run.py` (R3 debe mejorar); docs y tests primero.
- Fuera: rediseñar la tabla completa por task, tocar `capabilities.py`, dashboard.

## Restricciones

- El spec 7.2 y su estructura de tabla (tasks, tiers, bump 6.4) no se reescriben: se afinan las
  constantes y las filas que la medición contradice, cada una con comentario MEASURED y fecha.
- Filas y tests que la medición no contradice no se tocan.
- Todo cambio de fila sale de la matriz fechada, nunca de intuición.

## Configuración de pruebas

- TDD: activado. Runner `cd server && uv run pytest tests/test_policy*.py tests/test_eval_run.py -q`.
- Verificación de producto: `uv run --project server python eval/run.py` offline tras el cambio.

## Entrega

- Estrategia: ask-on-risk. Rama `feature/nau-14-eval` (continúa; NAU-14 ya está committeada).
- Líneas previstas: ~120.

## Tareas

- [x] NAU-18-T1 Constantes de latencia medidas por modelo en `policy.py` (`_score` las usa;
      fallback al prior por tier). Aceptación: a fit igual, gana el modelo medido más rápido.
      Riesgo RDD: medio
- [x] NAU-18-T2 Filas 7.2 por dificultad con ganadores medidos (d≤1 → deepseek-v4-flash; d==2 →
      glm5.3; default d≥3 sin tools → gemma4 primero). Aceptación: la decisión por banda refleja la
      escalera medida. Riesgo RDD: medio
- [x] NAU-18-T3 Re-evaluación offline de R3 y registro del antes/después en este doc.
      Aceptación: R3 mejora su tabla y queda documentado. Riesgo RDD: bajo

## Progreso

- Seguimiento (2026-11-14): el spec 6.4 («when in doubt, tier up») habla en tiers, pero con
  latencias medidas el escalón bumped de `chat_casual` d0 elige al mismo deepseek-v4-flash que el
  camino confiado — el bump ya no garantiza un modelo distinto, solo una escalera de tiers más
  alta. Seguimiento pendiente: reescribir 6.4 en lenguaje de capacidad medida (p. ej. «sube al
  mejor modelo medido por encima del elegido»), no de tiers. Interacción ligada (2026-11-14,
  comportamiento intencional de `_ID_FIT`/`_TIER_FIT`): en la fila d≥4 de `chat_casual`, el slot
  de tier 1 (t2_generalist, fit 1.0) supera al id medido en slot 2 (gemma4, fit 0.85), por lo que
  el segundo puesto lo decide la escalera medida y no la preferencia por id.
- Pendiente (2026-11-14, resuelto): `tests/test_eval_run.py::test_r3_replays_the_router_choice_per_prompt`
  fijaba el mundo hipotético («un chat trivial va a t1_fast»); alineado a la política medida con
  autorización expresa del usuario (superficie ampliada a ese único test): p0 ahora elige
  deepseek-v4-flash y la calidad del R3 sintético pasa de (4.0+3.0)/2 a (3.0+3.0)/2, con
  comentario MEASURED 2026-11-14. Suite completa: 72 passed.

## Log

| Tarea | Prueba | Resultado | Commit |
|---|---|---|---|
| T1 | `tests/test_policy.py::TestDecisionTable::test_measured_latency_reorders_t2_generalist_candidates`, `TestScoringSelectionAndFailOpen::test_candidate_order_is_stable_and_reproducible` | pass (24/24 en test_policy.py) | worktree, sin commit |
| T2 | `TestDecisionTable::test_row_chat_and_factual_easy_use_the_measured_fastest_model`, `::test_row_chat_and_factual_hard_prefers_the_measured_winners`, `TestWhenInDoubtTierUp::test_low_task_confidence_raises_exactly_one_tier` | pass | worktree, sin commit |
| T3 | `uv run --project server python eval/run.py --json eval/data/report.json` offline + suites `test_policy.py`/`test_capabilities.py`/`test_eval_matrix.py`/`test_eval_run.py` | R3 13382.4 → 13126.3 ms (−1.9 %), calidad 3.5714 constante, coverage 1.0; 72 passed; ruff y pyright en 0 | worktree, sin commit |

## Log

- **L1** (2026-11-14, usuario): «dale» sobre la propuesta de tres pasos (commit done, verificador
  T3 corriendo, afinamiento); elección del objetivo vía ask: «Latencia primero, piso de calidad».
- **L2** (2026-11-14, worker): tres tests alineados al mundo medido, solo expectativas:
  `test_row_chat_and_factual_hard_prefers_the_measured_winners` ahora construye la decisión con
  `needs_reasoning=True` y espera `(deepseek-v4-flash, qwen3.6)` — no `(…, gemma4)` como se
  anticipó: en la fila d≥4 el slot de tier 1 (`t2_generalist`, fit 1.0) supera al id medido en
  slot 2 (gemma4, fit 0.85); el lock-penalty no intervenía (el orden es idéntico con reasoning
  on u off). `test_low_task_confidence_raises_exactly_one_tier` reescrito con comentario MEASURED:
  confiado elige deepseek-v4-flash; inseguro (0.40, bump con ids dropeados) mantiene
  `t2_generalist`/`t3_max` y el mismo modelo — el lenguaje de tiers del 6.4 necesita rework
  medido (ver Progreso). `test_candidate_order_is_stable_and_reproducible` actualizado a la
  escalera medida `(deepseek-v4-flash, qwen3.6, glm5.3, mimo-v2.6-flash, gemma4, glm5.3-flash,
  qwen3.8-flash)`, verificada corriendo, con las aserciones de estabilidad intactas.
- **L3** (2026-11-14, worker): re-evaluación offline R3 (28 prompts, coverage 1.0): calidad
  3.5714 → 3.5714 (piso mantenido), latencia 13382.4 → 13126.3 ms (−256 ms, −1.9 %); el router
  cambia 6 prompts de gemma4 a qwen3.8-flash y estabiliza el resto. Mejora en la dirección
  esperada (latencia), modesta. `eval/data/report.json` regenerado por la corrida, nunca a mano.
  ruff (src, tests, eval) y pyright: 0 problemas. Suite completa: 72 passed (incluye
  `test_r3_replays_the_router_choice_per_prompt`, alineado con autorización expresa; ver
  Progreso).
