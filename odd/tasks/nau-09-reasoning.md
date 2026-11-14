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

- [x] NAU-09-T1 `togglable` (`qwen3.6`): `off` y `on` traducen a `chat_template_kwargs.enable_thinking`. Aceptación: dos tests, un parámetro cada uno. Riesgo RDD: medio
- [x] NAU-09-T2 `effort` (`deepseek-v4-flash`): `low`/`medium`/`high` traducen a `reasoning_effort`. Aceptación: tres tests, `off` no manda `reasoning_effort`. Riesgo RDD: medio
- [x] NAU-09-T3 `togglable_default_off` (`gemma4`): solo `on` y superiores activan. Aceptación: `low` se trata como apagado; `high` enciende. Riesgo RDD: medio
- [x] NAU-09-T4 `always_on` (`mimo-v2.6-flash`, `glm5.3-flash`, `glm5.3`, `qwen3.8-flash`): nunca se envían campos. Aceptación: un test por modelo, todos vacíos. Riesgo RDD: bajo
- [x] NAU-09-T5 Modelo desconocido y nivel desconocido: sin campos y sin excepción. Aceptación: ambos devuelven `{}`. Riesgo RDD: bajo

## Evidencia de verificación

| Tarea | Prueba | Resultado | Commit |
|---|---|---|---|
| T1 | `uv run pytest tests/test_reasoning.py` | RED de colección → 4 passed | `feat(router_core)` |
| T2 | idem | RED: 4 fallos con la rama `effort` en stub | `feat(router_core)` |
| T3 | idem | **RED con stub temporal**, no con función ausente: el módulo ya estaba completo | `feat(router_core)` |
| T4 | idem | RED con el mismo stub: 16 casos `always_on` | `feat(router_core)` |
| T5 | idem | RED con el stub, incluida la pureza (mutar el resultado corrompía la ficha) | `feat(router_core)` |
| suite | `uv run pytest` | 170 → **204 passed** | `feat(router_core)` |
| estático | `ruff check .` + `pyright` | 0 errores, 0 warnings | `feat(router_core)` |

### Tabla 7.4 verificada con la ficha real

```
glm5.3-flash       off     -> {}
qwen3.6            high    -> {'chat_template_kwargs': {'enable_thinking': True}}
qwen3.6            off     -> {'chat_template_kwargs': {'enable_thinking': False}}
deepseek-v4-flash  high    -> {'reasoning_effort': 'high'}
deepseek-v4-flash  off     -> {}
gemma4             low     -> {}
gemma4             high    -> {'chat_template_kwargs': {'enable_thinking': True}}
mimo-v2.6-flash    high    -> {}
hy4 (sin ficha)    high    -> {}
inventario intacto tras mutar el resultado: True
```

### Hallazgo que afecta al producto

La palanca de latencia que el spec llama "probablemente la mayor" — apagar el razonamiento en
tareas simples — **solo funciona en `gemma4` y `qwen3.6`**. De los tres modelos del escalón T1,
`qwen3.8-flash` y `glm5.3-flash` son `always_on`, así que una petición trivial que cae en
`glm5.3-flash` con `reasoning=off` en realidad razona igual. La política pide `off` y el modelo
lo ignora.

Decisión pendiente del humano: penalizar en la puntuación a los modelos `always_on` cuando la
política quiere `off`, para que el escalón T1 caiga en `gemma4`. Es un cambio de comportamiento
visible y toca constantes ya marcadas `[HIPÓTESIS]`.

## Revisión nativa (RDD)

| Tarea | Nivel | Resultado |
|---|---|---|
| T1-T5 | medio | pendiente de assess al cerrar el slice |

## Progreso

NAU-09 implementada y verificada contra la ficha real. Pendiente el merge.

## Siguiente paso

NAU-07: proxy `chat/completions` sin streaming.
