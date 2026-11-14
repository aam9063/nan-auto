# Guion demo — NaN Auto (hackathon)

> Video demo: **3–4 minutos**. Vista de app grabada + (opcional) intro/outro generados con
> HyperFrames. Mensaje central: *"un solo endpoint OpenAI-compatible; Clef elige el modelo y el
> nivel de razonamiento por petición, y lo probamos con datos medidos, no con promesas"*.

## Estado de la demo (antes de grabar)

- Gateway: `uv run python dev_server.py --port 8010` (en `server/`) — log demo limpio con 5
  decisiones variadas ya cargadas.
- Front: `GATEWAY_URL=http://127.0.0.1:8010 pnpm run dev` (en `web/`) → http://localhost:5176
- Reiniciar el log para grabar desde cero: borrar `server/dev_decisions.sqlite` y relanzar el
  gateway; el feed se repuebla solo con las llamadas de la demo.
- Capturas de referencia (para el video HyperFrames): `demo/01-*.png` a `demo/04-*.png`.

## Escena 1 — Hook (0:00–0:20)

**Pantalla**: Playground vacío.

**Locución**: «Toda app que usa LLMs enfrenta la misma decisión: ¿qué modelo llamo? El rápido
es tonto para lo difícil; el potente es lento y caro para lo trivial. NaN Auto convierte esa
decisión en un problema resuelto: un único endpoint OpenAI-compatible donde Clef clasifica cada
petición y el router elige modelo y razonamiento por petición.»

**Acción**: mostrar el logo/sidebar, sin tocar nada (2–3 s).

## Escena 2 — Playground: la decisión (0:20–1:10)

**Pantalla**: Playground.

**Acción + prompt** (pegar y pulsar «Decidir modelo»):

1. Trivial → modelo rápido, reasoning off:
   ```
   Hola, ¿qué tiempo hace hoy en Madrid?
   ```
2. Difícil → sube a reasoning high y cambia de modelo:
   ```
   Mi test falla con KeyError al leer la configuración. Te paso el stack trace y settings.py: ¿qué está pasando y cómo lo arreglo sin romper los otros entornos?
   ```
3. Generación de código → otro modelo otra vez:
   ```
   Escribí un helper async en Python que llame a un endpoint HTTP con reintentos ante 429 y backoff exponencial, con test incluido.
   ```

**Locución**: «Mirá "Lo que dijo Clef": tarea, dificultad, si necesita razonamiento — con
probabilidades. Y "Candidatos": por qué ganó ese modelo y por qué se descartaron los otros —
premium no permitido, tools en XML, etc. Nada de caja negra: la decisión viaja en el header
x-nan-auto-reason.»

**Detalle de cámara**: zoom suave a las barras de Clef y al chip «razonamiento: high».

## Escena 3 — Respuesta real (1:10–1:40)

**Pantalla**: Playground.

**Acción**: pegar la API key (paso previo, fuera del frame o difuminado), pulsar
«Enviar y ver la respuesta» con el prompt de código. Mostrar chips de **TTFT / Total / tokens**.

**Prompt alternativo en streaming** (para que se vea palabra por palabra):
```
Escribí un haiku sobre la lluvia de otoño en el campo.
```

**Locución**: «Y funciona de verdad: streaming incluido, con el primer token medido.»

## Escena 4 — Feed en vivo (1:40–2:20)

**Pantalla**: Feed en vivo.

**Acción**: dejar la tabla a la vista y lanzar desde el Playground (otra ventana o segunda
mitad de pantalla) el prompt del haiku con «Enviar». La decisión aparece en el feed sin
recargar (SSE). Clic en una fila → panel «Petición seleccionada» con las probabilidades de Clef.

**Locución**: «Cada decisión queda registrada: modelo elegido, nivel de razonamiento, costo de
decisión. El feed es SSE en vivo — mirá cómo entra esta.»

**Prompts extra para poblar el feed** (lanzar 2–3 rápido):
```
Tres amigos regentan una panadería. Ana decora 12 tortas por hora, Bruno hornea 20 panes por hora. ¿Cuántos productos salen en 6 horas?
```
```
Resume en 3 puntos: la migración de aves abarca miles de kilómetros y depende de factores genéticos y climáticos.
```

## Escena 5 — Registro (2:20–2:40)

**Pantalla**: Registro.

**Locución**: «El router solo elige entre modelos vivos: el registro lee el catálogo real de
NaN y cada modelo lleva su estado — disponible, degradado, sin cuota — y su cortacircuito. Si un
modelo se cae, el siguiente candidato entra sin avisar al cliente.»

**Detalle de cámara**: scroll lento por la tabla + pausa en «Cómo se lee».

## Escena 6 — Evaluación: la prueba (2:40–3:30)

**Pantalla**: Evaluación.

**Locución**: «Y esto no es marketing: medimos los 11 modelos del catálogo con una matriz de
calidad real — 28 prompts etiquetados, 307 respuestas generadas y puntuadas por un juez con
rúbrica. Con esa matriz evaluamos políticas sin gastar una llamada más. El router retiene el
90,9 % de la calidad del mejor modelo único y le gana un 17,7 % de latencia al "siempre el más
capaz", a 0,48 s de costo de decisión por petición.»

**Detalle de cámara**: KPIs primero, luego zoom al gráfico calidad-latencia (los puntos son los
11 modelos medidos; el verde es el router). Mencionar honestidad: «es una muestra de 28 prompts
y lo decimos en pantalla».

## Escena 7 — Cierre (3:30–4:00)

**Locución**: «NaN Auto: un endpoint, once modelos, cero decisiones manuales. Todo el stack —
router, juez, matriz de calidad y dashboard — corre en este repo.»

**CTA**: demo en vivo / repo / contact.

---

## Prompts de repuesto (por si algo falla en vivo)

| # | Prompt | Qué demuestra |
|---|--------|----------------|
| 1 | `¿Cuál es la diferencia entre TCP y UDP en una frase?` | factual_qa → rápido, off |
| 2 | `Traducí al inglés: el router elige modelo y nivel de razonamiento por cada petición.` | translate → rápido |
| 3 | `Planificá el deployment de un microservicio: leer Postgres, publicar eventos, avisar a Slack si falla.` | agentic_planning → tools/plan |
| 4 | `¿Me ayudás con esto? Tengo un problema raro con mi base de datos.` | confianza baja → bump de capability (6.4) |
| 5 | `Escribí un helper async en Python que llame a un endpoint HTTP con reintentos ante 429 y backoff exponencial.` | coding_generate con razonamiento |

## Notas para el video HyperFrames

- **Contexto visual para el generador**: tema claro, canvas gris cálido (#f2f4f1), tarjetas
  blancas con borde #e4e8e3, acento teal #0d7a5f, chip verde suave #dcece4, tipografía Inter
  (UI) + monospace (ids de modelo, métricas), texto casi negro #16191d. Capturas reales en
  `demo/01..04-*.png`.
- **Orden sugerido de b-rolls**: logo/sidebar (Escena 1) → barras de Clef (2) → chips
  TTFT/tokens (3) → tabla del feed apareciendo en vivo (4) → chips de estado del registro (5) →
  scatter con el punto teal del router (6) → KPIs de evaluación (cierre).
- **Números que no deben inventarse**: 11 modelos en el catálogo, 28 prompts etiquetados,
  307/308 respuestas medidas, 90,9 % retención, 17,7 % reducción de latencia vs R1, costo de
  decisión ~0,4–0,5 s (Clef), cobertura 100 %.
- **Frases prohibidas** (serían falsas): "el router supera a todos los modelos", "matriz de
  confusión", "comparación lado a lado de dos modelos" (esa pantalla no existe).
- Si el generador usa las capturas como fondo, recortar el sidebar en las escenas 2–4 para que
  el texto grande respire.

## Checklist de grabación

- [ ] Gateway corriendo (8010) y log demo poblado (o vacío, si se quiere poblar en vivo)
- [ ] Front en http://localhost:5176 (GATEWAY_URL=http://127.0.0.1:8010)
- [ ] API key pegada en el Playground antes de la Escena 3
- [ ] Ventana del navegador a 1440×900 o más (el layout es de dos columnas desde `xl`)
- [ ] Pestañas cerradas del playground anterior (5173–5175 ocupadas por instancias viejas)
