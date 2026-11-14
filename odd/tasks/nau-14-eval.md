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

- [ ] NAU-14-T1 Dataset de intenciones etiquetado, pequeño y honesto (≥ 20 prompts, es/en, cortos y largos, con y sin código). Aceptación: cada línea trae `id`, `prompt`, `task`, `difficulty`. Riesgo RDD: bajo
- [ ] NAU-14-T2 Generador de la matriz con cache SQLite por (prompt, modelo) y reanudación. Aceptación: dos ejecuciones seguidas no repiten llamadas. Riesgo RDD: medio
- [ ] NAU-14-T3 Limitador y backoff en el generador. Aceptación: nunca supera el límite configurado. Riesgo RDD: **alto**
- [ ] NAU-14-T4 Juez de calidad con rúbrica y orden de respuestas estable. Aceptación: puntuación reproducible sobre una respuesta dada. Riesgo RDD: medio
- [ ] NAU-15-T1 `eval/run.py`: baselines R0-R3 sobre la matriz, sin llamadas. Aceptación: tabla con calidad media, latencia media y cobertura por política. Riesgo RDD: medio
- [ ] NAU-15-T2 Datos del Pareto y de la retención de calidad. Aceptación: un punto por modelo único y uno por política. Riesgo RDD: bajo

## Evidencia de verificación

| Tarea | Prueba | Resultado | Commit |
|---|---|---|---|
| — | pendiente | — | — |

## Revisión nativa (RDD)

| Tarea | Nivel | Resultado |
|---|---|---|
| T1-T4, T1-T2 | medio (T3 alto) | verificador recomendado para T3 |

## Progreso

Documento abierto. **No se ejecuta la matriz completa sin decisión del humano**: son cientos de
llamadas con su clave.

## Siguiente paso

T1 y T2, más una ejecución mínima que demuestre que el harness funciona y cachea.
