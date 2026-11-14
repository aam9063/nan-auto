# NAU-10: fallback, circuit breaker y limitador de concurrencia

## Especificación (literal)

Del spec, sección 14:

> NAU-10 | Fallback, circuit breaker y limitador de concurrencia | Simulación de 429, 5xx y 402 | **Alto**

De la sección 5.5:

> Reglas:
> - Solo se cambia de modelo **antes del primer byte**. Después no se puede cambiar sin
>   duplicar contenido, así que el error se propaga al cliente.
> - Un **circuit breaker** por modelo: tras N fallos seguidos el modelo sale de la lista unos
>   segundos. Un 402 lo saca hasta la renovación de cuota.
> - Se respeta `Retry-After` en los 429.
> - Un limitador de concurrencia por clave evita agotar el máximo de peticiones simultáneas de
>   NaN. `[VERIFICAR si las peticiones a Clef y a los modelos de chat comparten el mismo límite de
>   la clave]`

De la sección 5.3 (pipeline):

> `fb{"Error antes del primer byte"}` → `nxt["Siguiente candidato, maximo 2 reintentos"]`

De la sección 8 (cabeceras):

> `x-nan-auto-fallback`: modelos intentados antes del definitivo, si hubo.

Estados del spec 5.5: `Selected → Calling → Streaming | Retry → Calling | Failed`, y
`Streaming → Done | StreamError`. Si se corta después del primer byte, no hay reintento.

## Objetivo

Que un modelo que falla no se lleve la petición por delante: reintentar con el siguiente
candidato **solo mientras no se haya enviado ningún byte**, sacar de circulación al que falla
repetidamente, respetar `Retry-After` y no reventar el límite de concurrencia de la clave.

## Problema y motivo

El clúster es compartido y los modelos caen: 429 por concurrencia, 402 por cuota, 5xx por
sobrecarga. Sin fallback, una petición perdida es un cliente roto. Con fallback mal hecho, se
duplica contenido —peor que fallar— o se reintenta contra un modelo que ya está caído y se
multiplica la carga.

## Alcance

- Dentro: circuit breaker por modelo (umbral, cooldown, 402 hasta renovación), selección del
  siguiente candidato, máximo de reintentos, respeto de `Retry-After`, limitador de concurrencia
  por clave, cabecera `x-nan-auto-fallback` con la cadena intentada.
- Fuera: el presupuesto de decisión de Clef con falla abierta (NAU-11), la estabilidad por
  conversación (NAU-12).

## Restricciones

- **Nunca** se cambia de modelo después de haber enviado bytes al cliente. Un corte a mitad del
  stream se propaga; no hay reintento silencioso.
- El circuit breaker es estado del proceso, por modelo, y no debe compartirse entre claves.
- Un 402 abre el breaker del modelo hasta la renovación de cuota, no unos segundos.
- `Retry-After` se respeta: si el servidor dice esperar N segundos, se espera o se pasa al
  siguiente candidato, pero no se reintenta antes.
- El limitador de concurrencia es **por clave** y debe liberar el permiso en todos los caminos,
  incluida la cancelación del cliente.
- La cabecera `x-nan-auto-fallback` lista los modelos intentados **antes** del definitivo, en
  orden, y solo aparece si hubo alguno.
- El fallback no puede saltarse el filtro de capacidades: solo se prueban candidatos que ya
  pasaron el filtro, en el orden que devolvió la política.
- Si se agotan candidatos y reintentos, se devuelve el último error con su código, no un 500
  genérico.

## Configuración de pruebas

- Modo TDD: activado. Runner `uv run pytest server/tests/test_fallback.py server/tests/test_proxy_fallback.py`.
- Fuente: sección 15.3 del spec.

## Entrega

- Estrategia: ask-on-risk. Rama `feature/nau-10-fallback`.
- Líneas previstas: ~450.
- Verificación: verificador independiente obligatorio (ítem 4: concurrencia; ítem 2: nada de la
  clave debe escapar por los nuevos caminos de error).

## Tareas

- [ ] NAU-10-T1 `CircuitBreaker`: umbral de fallos seguidos, cooldown, apertura por 402 hasta renovación, `allow(model_id)`. Aceptación: N fallos abren; el cooldown cierra; un 402 no cierra solo. Riesgo RDD: **alto**
- [ ] NAU-10-T2 Selección del siguiente candidato respetando el orden de la política y saltando los que el breaker cerró. Aceptación: con el primero abierto, gana el segundo del ranking. Riesgo RDD: **alto**
- [ ] NAU-10-T3 Fallback antes del primer byte: 429/5xx/402 → siguiente candidato, máximo 2 reintentos. Aceptación: tres escenarios con su cadena en `x-nan-auto-fallback`. Riesgo RDD: **alto**
- [ ] NAU-10-T4 Corte después del primer byte: **no** se reintenta, se propaga. Aceptación: un solo intento en el log, error al cliente. Riesgo RDD: **alto**
- [ ] NAU-10-T5 `Retry-After` en 429: no se reintenta antes de tiempo. Aceptación: el segundo intento ocurre después del plazo indicado. Riesgo RDD: **alto**
- [ ] NAU-10-T6 Limitador de concurrencia por clave, liberado en todos los caminos. Aceptación: 6 peticiones concurrentes con límite 2 nunca superan 2 en vuelo; cancelar una libera el permiso. Riesgo RDD: **alto**
- [ ] NAU-10-T7 Cabecera `x-nan-auto-fallback` en streaming y sin streaming, y su ausencia cuando no hubo fallback. Aceptación: los tres casos. Riesgo RDD: medio

## Evidencia de verificación

| Tarea | Prueba | Resultado | Commit |
|---|---|---|---|
| — | pendiente | — | — |

## Revisión nativa (RDD)

| Tarea | Nivel | Resultado |
|---|---|---|
| T1-T7 | alto | verificador independiente obligatorio |

## Progreso

Documento abierto.

## Siguiente paso

T1 y T2.
