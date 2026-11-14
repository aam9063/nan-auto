# NAU-02: DecisionProvider (adaptador `nan` + `mock`)

## Especificación (literal)

Del spec, sección 14:

> NAU-02 | `DecisionProvider`: cliente `nan` (semáforo de 2, reintento en 429) y `mock`. Primera llamada real y fixtures | Misma interfaz, parser probado con fixtures de respuesta real | Medio

Sección 6:

> **El formato del JSON de respuesta no está documentado.** Primera llamada de prueba con el cuestionario v1, respuesta real guardada en `server/tests/fixtures/clef/` y parser escrito contra esas fixtures `[VERIFICAR]`. Todo lo que dependa de ese formato vive en el adaptador `nan`.

> Concurrencia: Máximo 2 peticiones a la vez por persona. Si se supera, `429`: reintentar al momento

## Objetivo

Una única interfaz `DecisionProvider` con dos implementaciones intercambiables (`nan`, `mock`), un parser escrito contra respuestas reales y el cuestionario v1 en un solo sitio.

## Problema y motivo

Todo el router depende de leer bien la decisión. El formato de Clef no está documentado y el parser es el punto donde una suposición equivocada envenena la política entera. Escribirlo contra fixtures reales convierte esa incógnita en un test.

## Contrato real de Clef (verificado el 2026-11-14)

`POST https://api.nan.builders/v1/systemone`, `Authorization: Bearer $NAN_KEY`, cuerpo `{"model":"clef","state":"<json compacto como string>","questions":{...}}`.

Respuesta `200` (`object: structured_decision`):

```json
{
  "id": "decision-a45714e3bf94239e",
  "object": "structured_decision",
  "model": "clef",
  "answers": {
    "task": {"type": "choice", "choice": "coding_debug_or_review",
             "probabilities": {"coding_debug_or_review": 0.97, "...": 0.03}, "confidence": 0.97},
    "difficulty": {"type": "score", "score": 2.864,
                   "legend": {"0": "Trivial", "1": "Easy", "2": "Medium", "3": "Hard", "4": "Expert"},
                   "probabilities": {"2": 0.379, "3": 0.304, "4": 0.295}, "confidence": 0.379},
    "needs_reasoning": {"type": "noul", "noul": 0.840,
                        "probabilities": {"yes": 0.840, "no": 0.160}, "confidence": 0.840}
  },
  "usage": {"input_tokens": 500, "output_tokens": 0},
  "diagnostics": {"...": "label_mass 1.0, argmax_is_label true"},
  "joint": null
}
```

Hallazgos que corrigen el spec:

1. `criteria` de `noul` **no** es una lista: es un objeto con claves `true` y `false`, o `400`.
2. Las probabilidades de `score` vienen indexadas por **índice como string** (`"2"`), no por etiqueta; la etiqueta está en `legend`.
3. `needs_reasoning` entrega un único escalar `noul` (probabilidad de "sí"), no el mapa de etiquetas.
4. La confianza de un `score` es la masa del nivel modal (`0.379`), no `1.0`. Un umbral de confianza sobre `score` es engañoso: hay que mirar la forma de la distribución.
5. Latencia real medida desde el gateway: **520-570 ms** para los tres casos, por encima de los 300-500 del spec. El presupuesto de 800 ms deja poco margen.

Fixtures: `server/tests/fixtures/clef/systemone_v1_{coding_debug,chat_casual_es,creative}.json`.

## Alcance

- Dentro: interfaz `DecisionProvider`, tipos de decisión normalizados, parser contra las 3 fixtures, adaptador `nan` (semáforo configurable, reintento en 429, timeout), adaptador `mock` por reglas, cuestionario v1 y constructor de `state`.
- Fuera: caché de decisiones y política de puntuación (NAU-18 y NAU-06), presupuesto de 800 ms con falla abierta (NAU-11), integración en el proxy (NAU-07).

## Restricciones

- El parser no propaga el JSON crudo: si el formato cambia, el fallo es explícito.
- Ningún log ni fixture contiene la clave.
- El adaptador `mock` no hace red: permite CI y desarrollo offline.

## Configuración de pruebas

- Modo TDD: activado. Runner `uv run pytest server/tests/test_clef_*`.
- Fuente: sección 15.3 del spec.

## Entrega

- Estrategia: ask-on-risk. Rama `feature/nau-02-decision-provider`.
- Líneas previstas: ~450 (sin fixtures).
- Slices: un slice, un PR a `dev`.

## Tareas

- [ ] NAU-02-T1 Tipos normalizados (`TaskAnswer`, `ScoreAnswer`, `NoulAnswer`, `Decision`) e interfaz `DecisionProvider`. Aceptación: el protocolo se importa y el mock lo satisface. Riesgo RDD: medio
- [ ] NAU-02-T2 Parser contra las 3 fixtures, incluidos `legend` e índices string. Aceptación: un test por fixture lee `task`, `difficulty`, `needs_reasoning` y `expected_output_length` con los valores reales. Riesgo RDD: medio
- [ ] NAU-02-T3 Cuestionario v1 y constructor de `state` acotado (600/200/150 tokens). Aceptación: snapshot del cuestionario y recorte de un mensaje largo. Riesgo RDD: medio
- [ ] NAU-02-T4 Adaptador `nan`: semáforo, reintento en 429, timeout, errores tipados. Aceptación: tests con respx de 200, 429 y 401 y de timeout. Riesgo RDD: medio
- [ ] NAU-02-T5 Adaptador `mock` por reglas. Aceptación: mismo contrato que `nan`, sin red. Riesgo RDD: bajo

## Evidencia de verificación

| Tarea | Prueba | Resultado | Commit |
|---|---|---|---|
| fixtures | 3 llamadas reales a `/v1/systemone` | 200, 452-570 ms | 0ebc3ea |

## Revisión nativa (RDD)

| Tarea | Nivel | Resultado |
|---|---|---|
| T1-T5 | medio | pendiente de assess al cerrar el slice |

## Progreso

Fixtures capturadas y contrato verificado. Implementación pendiente.

## Siguiente paso

T1 y T2: tipos y parser contra fixtures.
