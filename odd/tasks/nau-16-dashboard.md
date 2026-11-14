# NAU-16b/17: dashboard React

## Especificación (literal)

Del spec, sección 10:

| Pantalla | Contenido |
|---|---|
| **Playground** | Cajón de texto y adjuntos. Muestra las probabilidades de Clef por pregunta, los candidatos descartados con el motivo, la puntuación y el modelo elegido. Botón "enviar" para ver la respuesta real |
| **Feed en vivo** | Peticiones en tiempo real con modelo, razonamiento, TTFT, total, fallbacks |
| **Comparación** | Mismo prompt enviado a la elección del router y a una referencia (por ejemplo el modelo más grande), en paralelo. Muestra TTFT, tiempo total, tokens y las dos respuestas. Veredicto opcional de un juez |
| **Registro** | Estado de cada modelo: disponible, salud, cuota, escalón |
| **Evaluación** | Gráfica de Pareto de calidad frente a latencia, tabla de baselines, matriz de confusión de `task` |

> Stack (carpeta `web/`): React 19 con TypeScript (TSX), Vite y Tailwind CSS v4, gestionado con pnpm. SSE con `EventSource` para tiempo real.

Del spec, sección 17 (guion de demo):

> Playground con tres prompts: un saludo, un bug difícil y una imagen. Cada uno va a un modelo distinto y se ven las probabilidades de Clef

## Objetivo

Una SPA que haga visible la decisión: por qué este modelo y no otro, qué se descartó y por qué, y
cuánto costó.

## Problema y motivo

Es la pantalla que convence al jurado. Sin ella, el router es una caja negra que acierta por
casualidad.

## Alcance

- Dentro: cliente de API tipado, playground con probabilidades y descartes, feed en vivo por SSE,
  registro de modelos, y una vista de evaluación preparada para leer la matriz cuando exista.
- Fuera: comparación lado a lado con juez (necesita NAU-15), y subida real de adjuntos (el
  playground acepta texto; las imágenes llegan por el cliente OpenAI, no por aquí).

## Restricciones

- **Nada de datos falsos.** Si una métrica no existe todavía, se muestra vacía con su motivo, no
  inventada. Una gráfica con números inventados destruye la credibilidad de la demo.
- Sin claves en el navegador: la clave se pide una vez en el playground y vive en memoria de la
  sesión, nunca en `localStorage`.
- La API se consume por rutas relativas (`/v1/...`): Vite ya proxea al gateway.
- Todo dato mostrado viene del servidor; nada se calcula en el cliente salvo formato.
- Tipos de la API generados con `openapi-typescript` cuando el gateway esté levantado; mientras
  tanto, tipos escritos a mano y marcados como tales.

## Configuración de pruebas

- Modo TDD: excepción documentada (spec 15.3, fila `web`). Runner:
  `pnpm --dir web typecheck && pnpm --dir web lint && pnpm --dir web build` más prueba de humo
  manual con el gateway arrancado.

## Entrega

- Estrategia: ask-on-risk. Rama `feature/nau-16-dashboard`.
- Líneas previstas: ~700.

## Tareas

- [ ] NAU-16b-T1 Cliente de API y tipos (`/v1/playground/decide`, `/v1/decisions`, `/v1/registry`). Aceptación: si el gateway no responde, la UI muestra el error, no un espinner infinito. Riesgo RDD: bajo
- [ ] NAU-16b-T2 Playground: prompt → decisión con probabilidades por pregunta, descartes con motivo y modelo elegido. Aceptación: tres prompts del guion dan tres modelos distintos. Riesgo RDD: bajo
- [ ] NAU-16b-T3 Feed en vivo por `EventSource` sobre `/v1/stream`, con estado de conexión. Aceptación: una petición externa aparece sin recargar. Riesgo RDD: bajo
- [ ] NAU-17-T1 Registro de modelos con disponibilidad, salud, cuota y escalón. Aceptación: refleja `/v1/registry`. Riesgo RDD: bajo
- [ ] NAU-17-T2 Vista de evaluación con Pareto y baselines leyendo la matriz cuando exista, y un estado vacío honesto cuando no. Aceptación: sin datos, lo dice. Riesgo RDD: bajo

## Evidencia de verificación

| Tarea | Prueba | Resultado | Commit |
|---|---|---|---|
| — | pendiente | — | — |

## Revisión nativa (RDD)

| Tarea | Nivel | Resultado |
|---|---|---|
| T1-T5 | bajo | sin verificación independiente |

## Progreso

Documento abierto.

## Siguiente paso

T1 y T2.
