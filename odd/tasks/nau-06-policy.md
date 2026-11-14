# NAU-06: filtro de capacidades y política v1

## Especificación (literal)

Del spec, sección 14:

> NAU-06 | Filtro de capacidades y política v1 (tabla) | Tabla de decisión cubierta con tests | Medio

De la sección 3:

> Política (código) | Puntuación de candidatos, perfil, estabilidad de conversación, fallback | Reglas editables y testeables

De la sección 7.2, tabla de decisión inicial `[HIPÓTESIS]`:

| Tarea | Dificultad | Candidatos (en orden) | Razonamiento |
|---|---|---|---|
| `chat_casual`, `factual_qa` | 0 a 1 | T1 | off |
| `chat_casual`, `factual_qa` | 2 | T2 | off |
| `translate_or_rewrite` | 0 a 2 | T1, T2 | off |
| `summarize_or_extract` | cualquiera | Si hay contexto largo: `deepseek-v4-flash`, `glm5.3-flash`. Si no, T1 o T2 | off |
| `creative_writing` | cualquiera | T2 | off |
| `coding_generate` | 0 a 2 | T2 | off o low |
| `coding_generate` | 3 a 4 | `glm5.3` si el perfil lo permite, si no `deepseek-v4-flash` con high o `qwen3.6` con razonamiento | on |
| `coding_debug_or_review` | 2 a 4 | T2 o T3 | on |
| `math_or_logic` | 2 a 4 | T2 o T3 | on |
| `agentic_planning` o `has_tools` | cualquiera | T2 o T3 (evitar modelos con tool calling XML sin verificar) | según `needs_reasoning` |

De la sección 7.3:

| Perfil | `q_weight` | `l_weight` | Premium | Razonamiento |
|---|---|---|---|---|
| `nan-auto` (balanceado) | 1.0 | 0.5 | Solo si dificultad ≥ 3 | Según `needs_reasoning` |
| `nan-auto-fast` | 0.6 | 1.0 | No | Off salvo dificultad ≥ 4 |
| `nan-auto-quality` | 1.0 | 0.2 | Sí | On si dificultad ≥ 2 |

De la sección 6.4:

> Si `task.confidence < 0.60`, o si la distribución de `difficulty` está repartida entre "fácil" y "difícil" (masa de probabilidad alta en ambos extremos), no hay segunda consulta: la política aplica **ante la duda, subir de escalón** (más capacidad, no menos).

De la sección 5.3:

> "no respondió a tiempo" hace que NaN Auto **falle abierto**: si Clef tarda, se usa el modelo por defecto del perfil

## Objetivo

Dos funciones puras: una que reduce el inventario a candidatos posibles por hechos, y otra que, con la decisión de Clef, elige **modelo + nivel de razonamiento** y explica el motivo.

## Problema y motivo

Es el corazón del producto. Si el filtro deja pasar un modelo sin visión, la petición con imagen falla. Si la política elige mal, la demo no supera su propia línea de Pareto.

## Alcance

- Dentro: filtro por modalidad, contexto, tools, formato de salida y premium; tabla de decisión v1; regla "ante la duda, subir"; puntuación de candidatos; perfil; motivo legible.
- Fuera: estabilidad conversacional (NAU-12), fallback y circuit breaker (NAU-10), traducción del nivel de razonamiento al parámetro de cada modelo (NAU-09), matriz de calidad medida (NAU-14).

## Restricciones

- Funciones puras: entran features, inventario, decisión y perfil; sale una elección. Sin red, sin reloj, sin estado global.
- El motivo (`reason`) es corto y no contiene texto del usuario, solo hechos: por ejemplo `coding_debug_or_review d=3 reasoning=on`.
- El nivel de razonamiento es abstracto (`off`, `low`, `medium`, `high`); traducirlo a parámetros concretos es NAU-09.
- Nunca elegir un modelo marcado `excluded` ni uno no disponible por salud o cuota.
- Un perfil desconocido no debe reventar: cae al balanceado.

## Configuración de pruebas

- Modo TDD: activado. Runner `uv run pytest server/tests/test_policy.py server/tests/test_capabilities.py`.
- Fuente: sección 15.3 del spec.

## Entrega

- Estrategia: ask-on-risk. Rama `feature/nau-06-policy`.
- Líneas previstas: ~450.

## Tareas

- [ ] NAU-06-T1 Filtro por modalidad: imagen y audio solo van a modelos que los aceptan. Aceptación: con `has_image`, `glm5.3` (solo texto) queda fuera con motivo. Riesgo RDD: medio
- [ ] NAU-06-T2 Filtro por contexto: `est_tokens` por encima de `max_input_tokens` descarta. Aceptación: un prompt gigante deja fuera al modelo pequeño. Riesgo RDD: medio
- [ ] NAU-06-T3 Filtro por tools y formato: con `tools`, los modelos de tool calling XML se penalizan; con `json_schema`, se descarta quien no lo soporta. Aceptación: ambos casos con motivo explícito. Riesgo RDD: medio
- [ ] NAU-06-T4 Premium y exclusión por perfil: `nan-auto-fast` no usa premium; `excluded` nunca se elige. Aceptación: tres perfiles con el mismo inventario dan candidatos distintos. Riesgo RDD: medio
- [ ] NAU-06-T5 Tabla de decisión v1 completa, fila por fila. Aceptación: una prueba por fila de la tabla del spec. Riesgo RDD: medio
- [ ] NAU-06-T6 Regla "ante la duda, subir": `task.confidence < 0.60` o dificultad repartida suben un escalón. Aceptación: dos casos, cada uno sube exactamente uno. Riesgo RDD: medio
- [ ] NAU-06-T7 Puntuación y elección: `score = fit*q - latency*l - quota_penalty`, con el motivo legible y el fallo abierto al modelo por defecto. Aceptación: el orden de candidatos es estable y reproducible. Riesgo RDD: medio

## Evidencia de verificación

| Tarea | Prueba | Resultado | Commit |
|---|---|---|---|
| — | pendiente | — | — |

## Revisión nativa (RDD)

| Tarea | Nivel | Resultado |
|---|---|---|
| T1-T7 | medio | pendiente de assess al cerrar el slice |

## Progreso

Documento abierto.

## Siguiente paso

T1 a T7.
