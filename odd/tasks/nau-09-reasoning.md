# NAU-09: adaptador de razonamiento por modelo

## Especificación (literal)

Del spec, sección 14:

> NAU-09 | Adaptador de razonamiento por modelo | Test por modelo del parámetro enviado | Medio

De la sección 7.4:

| Modelo | Traducción |
|---|---|
| `qwen3.6` | `chat_template_kwargs: { enable_thinking: false \| true }` |
| `deepseek-v4-flash` | `reasoning_effort: low \| medium \| high` |
| `gemma4` | `chat_template_kwargs: { enable_thinking: true }` solo para `on` |
| `mimo-v2.5`, `glm5.3-flash` | Siempre activo, sin cambio |
| `qwen3.8-flash`, `glm5.3` | Sin interruptor documentado, sin cambio |

De la sección 5.2:

> Adaptador de razonamiento | Traduce "razonamiento on/off/nivel" al parámetro propio de cada modelo

## Objetivo

Una función pura que, dado un modelo y un nivel abstracto (`off`, `low`, `medium`, `high`), devuelva los campos exactos que hay que añadir al cuerpo reenviado a NaN.

## Problema y motivo

La política decide en abstracto; cada modelo enciende y apaga el razonamiento de una forma distinta y algunos no lo permiten. Sin este adaptador, apagar el razonamiento —la mayor palanca de latencia del proyecto— no funciona.

## Alcance

- Dentro: traducción por modelo a partir de la ficha (`config/models.yaml`), tests por modelo del parámetro enviado, comportamiento ante modelo desconocido.
- Fuera: decidir el nivel (NAU-06, hecho), el reenvío al proxy (NAU-07/08), la medición de la ganancia (NAU-14).

## Restricciones

- Función pura: no muta el cuerpo original, devuelve el diccionario de campos a fusionar.
- Un modelo sin interruptor no recibe campos, ni siquiera si la política pidió `high`.
- Un nivel desconocido cae a `off` sin reventar.
- El modelo desconocido no debe recibir parámetros inventados: sin ficha, sin cambios.
- Los nombres de campo salen de la ficha (`reasoning.mode`, `reasoning.off`, `reasoning.on`), no de código hardcodeado por id.

## Configuración de pruebas

- Modo TDD: activado. Runner `uv run pytest server/tests/test_reasoning.py`.
- Fuente: sección 15.3 del spec.

## Entrega

- Estrategia: ask-on-risk. Rama `feature/nau-09-reasoning`.
- Líneas previstas: ~180.

## Tareas

- [ ] NAU-09-T1 `togglable` (`qwen3.6`): `off` y `on` traducen a `chat_template_kwargs.enable_thinking`. Aceptación: dos tests, un parámetro cada uno. Riesgo RDD: medio
- [ ] NAU-09-T2 `effort` (`deepseek-v4-flash`): `low`/`medium`/`high` traducen a `reasoning_effort`. Aceptación: tres tests, `off` no manda `reasoning_effort`. Riesgo RDD: medio
- [ ] NAU-09-T3 `togglable_default_off` (`gemma4`): solo `on` y superiores activan. Aceptación: `low` se trata como apagado; `high` enciende. Riesgo RDD: medio
- [ ] NAU-09-T4 `always_on` (`mimo-v2.6-flash`, `glm5.3-flash`, `glm5.3`, `qwen3.8-flash`): nunca se envían campos. Aceptación: un test por modelo, todos vacíos. Riesgo RDD: bajo
- [ ] NAU-09-T5 Modelo desconocido y nivel desconocido: sin campos y sin excepción. Aceptación: ambos devuelven `{}`. Riesgo RDD: bajo

## Evidencia de verificación

| Tarea | Prueba | Resultado | Commit |
|---|---|---|---|
| — | pendiente | — | — |

## Revisión nativa (RDD)

| Tarea | Nivel | Resultado |
|---|---|---|
| T1-T5 | medio | pendiente de assess al cerrar el slice |

## Progreso

Documento abierto.

## Siguiente paso

T1 a T5.
