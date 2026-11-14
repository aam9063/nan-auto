# NaN Auto — informe de desarrollo

> Documento vivo. Registra qué se construye, cómo, **qué se rompió en el camino y cómo se
> detectó y resolvió**. Se actualiza al final de cada unidad de trabajo.
>
> Última actualización: 2026-11-14 · rama `dev` en `a64fc2f` · 231 tests en verde.

---

## 1. Objetivo

Construir **NaN Auto**: un gateway que se presenta como un modelo más (`model: "nan-auto"`) y,
por detrás, elige para cada petición qué modelo de NaN responde y con cuánto razonamiento.

El objetivo declarado no es el dinero —NaN es tarifa plana— sino cuatro cosas medibles:

| Objetivo | Cómo se comprueba |
|---|---|
| Latencia (TTFT y total) | frente a "siempre el modelo más grande" |
| Calidad suficiente | retención vs el mejor modelo único |
| Cuotas escasas | peticiones que evitan modelos premium |
| Carga del clúster | tokens de razonamiento evitados en tareas triviales |

La frase que sostiene la demo: *"NaN tiene siete modelos y cada uno es mejor en algo. NaN Auto
elige el adecuado en menos de medio segundo, apaga el razonamiento cuando no hace falta y te
enseña la calidad contra la latencia."*

**La decisión que hace posible esa frase** está en la sección 5.3 de este informe: hoy, medido,
el gateway apaga el razonamiento de verdad en tareas triviales. No siempre fue así.

---

## 2. Cómo se trabaja

- **ODD** (Organic Driven Development) como flujo: autorizar → explorar → resolver dudas →
  clasificar → registrar antes de escribir → implementar tarea a tarea → cerrar con evidencia.
- **Un documento de feature por unidad** en `odd/tasks/`, con la especificación **literal**
  copiada del diseño, las tareas con criterio de aceptación y la tabla de evidencia.
- **Ramas cortas** (`feature/*`, `fix/*`, `chore/*`) y merge `--no-ff` a `dev`. `main` no se toca:
  lo fusiona Albert a mano.
- **Commits convencionales** que explican el *porqué*, con tests y documentación en el mismo
  commit que el comportamiento.
- **Test-first** donde hay test ejecutable: se observa RED, luego GREEN. Cuando no se observó
  RED, se dice explícitamente y se compensa con mutación (ver error #9).
- **Verificador independiente** cuando el cambio toca un ítem de riesgo alto. Revisa una copia
  de solo lectura, no el árbol vivo, y su trabajo es **refutar**, no confirmar.

---

## 3. Estado

| Feature | Qué hace | Estado | Commit |
|---|---|---|---|
| NAU-01 | Estructura, CI, hooks, plantillas | hecho | `371ab52` |
| NAU-02 | `DecisionProvider`: contrato real de Clef, parser, `nan` + `mock` | hecho + verificado | `8d642ec` |
| NAU-03 | Registro de modelos desde `/v1/models` + ficha | hecho | `55dbfc4` |
| NAU-04 | Extractor de features deterministas | hecho | `af39990` |
| NAU-05 | Cuestionario v1 y `state` acotado | hecho (dentro de NAU-02) | `8d642ec` |
| NAU-06 | Filtro de capacidades y política v1 | hecho | `44029f4` |
| NAU-07/08 | Proxy `chat/completions` + SSE, cabeceras `x-nan-auto-*` | hecho + verificado | `a64fc2f` |
| NAU-09 | Adaptador de razonamiento por modelo | hecho | `28e2c37` |
| — | Calibración medida del razonamiento | hecho | `bc95661` |
| — | Preferencia por modelos que respetan `off` | hecho | `2e18f03` |
| NAU-10 | Fallback y circuit breaker | pendiente | — |
| NAU-13 | Log de decisiones sin contenido | pendiente (primer intento falló) | — |
| NAU-14/15 | Benchmark y evaluación offline | pendiente | — |
| NAU-16/17 | Dashboard | pendiente | — |
| NAU-12/11 | Estabilidad conversacional y presupuesto de decisión | pendiente | — |

**Números reales**, no estimados: 231 tests, `ruff` limpio, `pyright` 0 errores.

---

## 4. Implementación

### 4.1 Contrato con Clef (NAU-02) — lo que el diseño no sabía

El formato de respuesta de Clef **no está documentado**. Se resolvió llamando de verdad a la
API y escribiendo el parser contra las respuestas capturadas en `server/tests/fixtures/clef/`.

Contrato verificado:

```json
{"object":"structured_decision","answers":{
  "difficulty":{"type":"score","score":2.91,
    "legend":{"0":"Trivial","1":"Easy","2":"Medium","3":"Hard","4":"Expert"},
    "probabilities":{"2":0.369,"3":0.272,"4":0.334},"confidence":0.369}}}
```

Cuatro correcciones al diseño:

1. `noul` con `criteria` como lista devuelve **400**; debe ser un objeto `{true, false}`.
2. `score` indexa las probabilidades por **índice como string** (`"2"`), y la etiqueta vive en
   `legend`. Suponer claves por etiqueta habría dado decisiones mudas.
3. `needs_reasoning` devuelve una **probabilidad escalar** de "sí", no un mapa de etiquetas.
4. La `confidence` de un `score` es la **masa modal** (0.37), no certeza. Un umbral sobre ese
   número engaña, así que el tipo expone `is_split()`: la regla "ante la duda, subir de
   escalón" mira la **forma** de la distribución, no solo el máximo.

Latencia medida: **450–570 ms** por decisión, por encima de los 300–500 del diseño. El
presupuesto de 800 ms de NAU-11 queda con poco margen: dato para tener presente.

### 4.2 Registro de modelos (NAU-03)

Se construye del catálogo vivo fusionado con `config/models.yaml`. Contra la API real:

```
catálogo vivo: 18  →  servibles para texto: 12
excluidos: flux-2-klein, kokoro, qwen-image-2.1, qwen3-embedding, rerank, whisper
```

La regla que importa: **la ficha local gana sobre el campo `mode`**. `flux-2-klein` y
`qwen-image-2.1` se anuncian como `mode: chat` y son generadores de imagen; un registro que
confiara en la API habría mandado prompts de texto a un generador de imágenes.

Un id desconocido **nunca se descarta**: entra como generalista sin escalón. Un modelo nuevo
funciona sin tocar código.

### 4.3 Features, filtro y política (NAU-04, NAU-06)

El reparto de responsabilidades del diseño se respeta: **si se puede decidir con código, se
decide con código**. El extractor devuelve solo hechos (¿hay imagen?, ¿audio?, ¿tools?, ¿qué
formato?), y una prueba canario demuestra que **ningún texto del usuario sale** de la función:
solo booleanos, cuentas y un hash.

El filtro descarta por hecho con un código de motivo (`no_audio_support`, `context_too_small`,
`premium_not_allowed`, …) y la política implementa la tabla del diseño fila por fila.

### 4.4 Razonamiento (NAU-09 + calibración)

Aquí está la mayor sorpresa del proyecto y el error más caro (sección 5.2). Resumen: la ficha
del diseño estaba equivocada en tres de cinco modelos, y el razonamiento **se estaba comiendo
las respuestas**.

### 4.5 Proxy y streaming (NAU-07/08)

`POST /v1/chat/completions` compone todo y reenvía. Verificado con HTTP real:

```
POST /v1/chat/completions  {"model":"nan-auto", ...}
→ 200  x-nan-auto-model: gemma4
       x-nan-auto-decision-id: decision-mock
       x-nan-auto-reason: chat_casual d=0 reasoning=off
stream:true → 200, 50 eventos SSE reenviados en vivo
```

La clave del cliente solo viaja de cabecera entrante a cabecera saliente. Un modelo que no es
alias se reenvía **byte a byte**, sin decidir nada.

---

## 5. Errores cometidos, detección y resolución

Esta es la sección que más justifica el trabajo: **ninguno de estos errores se descubrió
leyendo el código, sino probándolo de verdad**. Se documentan con el mismo detalle los que
detectó un humano, los que detectó un verificador y los que detectó una medición.

### 5.1 El `.env` no se leía (silencioso)

**Síntoma:** la clave "estaba puesta" pero el cliente mandaba `Bearer ` vacío.

**Causa:** `Settings` buscaba `.env` en el *cwd*. Corriendo desde `server/`, el fichero de la
raíz no existía… y `pydantic-settings` **no avisa**: usa el valor por defecto.

**Detección:** la primera llamada real falló con `Illegal header value b'Bearer '`. Ningún test
lo habría visto, porque los tests no leen secretos.

**Resolución:** `env_file=(".env", "../.env")` y un test con `monkeypatch.chdir` que reproduce
el escenario real (ejecutar desde `server/` y encontrar el fichero del padre).

**Lección:** un valor por defecto silencioso convierte un error de configuración en un
comportamiento plausible. El bug no era "no lee el fichero", era "no dice que no lo lee".

### 5.2 El razonamiento se comía las respuestas (el más caro)

**Síntoma:** el gateway respondía `200` con `content: null` y `finish_reason: "length"`.

**Causa raíz, en dos capas:**

1. **La documentación miente por omisión.** Dice que `gemma4` está "desactivado por defecto".
   Medido: sin parámetros **razona igual** (72 de 80 tokens, 1763 ms) y deja el contenido vacío.
   "Desactivado por defecto" significaba *hay que mandar `false` explícito*.
2. **La política pedía `off` y no comprobaba si el modelo podía obedecer.** Dos de los tres
   modelos del escalón rápido ignoran la petición; el proveedor sigue gastando latencia.

**Detección:** la prueba de humo HTTP real con un saludo trivial. Ningún test unitario lo habría
visto, porque los tests usaban la ficha… que estaba mal. **El test heredaba la suposición
equivocada.**

**Resolución, en dos pasos:**

*Paso 1 — medir modelo por modelo* (mejor de 2 ejecuciones, prompt trivial, `max_tokens=80`):

| Modelo | Sin parámetros | Con `enable_thinking:false` | La ficha decía |
|---|---|---|---|
| `gemma4` | 1763 ms · 72 tok · **content null** | **877 ms · 0 tok** · contenido ok | "off por defecto" ❌ |
| `qwen3.8-flash` | 980 ms · 34 tok | **908 ms · 0 tok** | `always_on` ❌ |
| `qwen3.6` | 909 ms · 77 tok | 884 ms · 0 tok | correcto ✓ |
| `glm5.3-flash` | 3363 ms · 66 tok | 4259 ms · 58 tok | `always_on` ✓ |
| `deepseek-v4-flash` | 1063 ms · 38 tok | `effort:low` → 41 tok | "effort" no apaga ❌ |

*Paso 2 — codificar la medición:* la ficha se corrigió **con la medición citada en el propio
YAML**, y se añadieron tests que cargan la ficha real (`load_card()`), de modo que restaurar las
suposiciones viejas rompe la suite.

*Paso 3 — que la política obedezca la realidad:* `honours_off()` es la única fuente de verdad
sobre si un interruptor existe (solo los modos *togglable* responden a `false`). La puntuación
penaliza a los modelos que no pueden obedecer cuando se pide `off`.

**Resultado medido:** el mismo saludo pasa de `content: null` / 72 tokens de razonamiento a
**0 tokens de razonamiento, `finish_reason: stop` y contenido real**. Y en T2, una pregunta
factual cae en `qwen3.6` en vez de en `deepseek-v4-flash`, que no puede apagar el razonamiento.

**Lección de método:** una medición vale más que una tabla de documentación, y **un test que
copia una suposición equivocada no verifica nada**. Los tests ahora leen la ficha real.

### 5.3 `true` aceptado como número (lo encontró el verificador)

**Síntoma:** ninguno visible. Un payload con `"score": true` se parseaba como `1.0`.

**Causa:** en Python `bool` es subclase de `int`, así que `isinstance(x, int | float)` acepta
`True`. La validación *parecía* estricta.

**Detección:** el verificador independiente recibió el encargo explícito de **refutar**, no
confirmar. Construyó el payload y lo demostró. Su veredicto fue `REFUTED`, no un "se ve bien".

**Resolución:** un guardián `_number()` que rechaza `bool` primero, aplicado a **todos** los
campos numéricos (`score`, `noul`, masas, `confidence`), con 6 tests de regresión.

**Lección:** `bool ⊂ int` es una trampa clásica de Python y la revisión por pares la encuentra en
minutos. La auto-revisión no la habría encontrado: yo escribí ese código y estaba convencido.

### 5.4 La clave podía filtrarse por tres caminos (dos rondas de verificación)

Este es el error más interesante porque **la primera corrección no alcanzó**.

**Camino 1 — el eco del servidor.** El parser construía el mensaje de error con el texto que
mandaba el servidor: `Clef error BadRequestError: <mensaje>`. Si el servidor alguna vez embebe
datos de la petición en su mensaje, la clave entera acaba en un log. **Resolución:** la excepción
lleva **solo el tipo**, nunca el mensaje.

**Camino 2 — truncar antes de redactar.** El camino 4xx hacía `self._redact(response.text[:200])`.
Si la clave cae en la columna 187, la rebanada se lleva solo su cola: la redacción ya no la
reconoce y quedan 13 caracteres en el mensaje. **Resolución:** redactar **primero**, truncar
después.

**Detección:** primera ronda del verificador para el camino 1; segunda ronda, con la instrucción
de atacar la redacción, para el camino 2. Ambas con una clave canario propia.

**Lección — la más importante de todo el informe:** *la redacción de secretos necesita un test
adversario.* Los tests originales comprobaban "el cuerpo de error no contiene la clave", que es
exactamente el caso que un servidor defectuoso **no** produce. Un test que solo prueba el
comportamiento correcto no es un test de seguridad.

### 5.5 Excepciones que rompían el fallo abierto

**Síntoma:** ninguno hasta que ocurriera en producción.

**Causa:** un cuerpo `200` mal formado lanzaba `JSONDecodeError`, un payload en lista
`AttributeError`, una masa no numérica `ValueError`. Ninguna es `ClefError`, así que el
llamador que hace *fail-open* no las capturaba: la petición fallaba en vez de degradar.

**Detección:** el verificador, enumerando los tipos de escape.

**Resolución:** una jerarquía en `errors.py` con todo colgando de `ClefError`, y normalización
de los cuerpos rotos. Además: `json.loads` acepta `NaN` e `Infinity`, que ahora se rechazan con
`math.isfinite` (si no, `ValueError` al redondear).

### 5.6 Cinco commits aterrizaron en `dev` en vez de en su rama

**Síntoma:** `git branch --show-current` decía `dev` con trabajo que debía estar en una feature.

**Causa:** el `git switch -c feature/...` estaba en el **mismo comando** que un `git commit` cuyo
mensaje contenía `.env`. El guardia de seguridad del harness bloqueó el comando entero; como
`&&` es secuencial, **no se ejecutó nada**, ni siquiera el `switch`. Los commits siguientes
fueron a `dev`.

**Detección:** al intentar `git push -u origin feature/nau-02-decision-provider`, git respondió
`src refspec ... does not match any`. El push fue el que delató al switch que nunca ocurrió.

**Resolución sin reescribir historia:**

```bash
git branch feature/nau-02-decision-provider   # la rama nace en HEAD
git switch feature/nau-02-decision-provider   # HEAD se mueve a ella
git branch --force dev origin/dev             # dev vuelve a su origen
```

Ningún commit se perdió y no hizo falta `reset --hard`. **Lección:** un comando compuesto es
atómico respecto del bloqueo; el fallo es silencioso porque el `&&` no llegó a correr. Y un
`git push` que falla por refspec inexistente es una señal de topología, no de red.

### 5.7 YAML 1.1 convierte `on:` y `off:` en booleanos

**Síntoma:** el interruptor de razonamiento de la ficha llegaba **vacío**.

**Causa:** YAML 1.1 (el que usa `pyyaml`) interpreta las claves desnudas `on`/`off` como `true`/
`false`. `reasoning.on` nunca existía como string.

**Detección:** el worker que implementó el registro, al ver que el resultado salía vacío.

**Resolución:** `_reasoning_branch` consulta la clave como string **y** como booleano.

**Lección:** un formato de configuración "obvio" puede tener semántica sorpresa. Vale más un
test que lea el fichero real que cinco que construyan el objeto a mano.

### 5.8 `minimax-h3` se colaba en la política

**Síntoma:** un modelo marcado `excluded_from_policy: true` aparecía como candidato.

**Causa:** el registro leía `premium` de la ficha pero **ignoraba** `excluded_from_policy`. La
clave existía en el YAML y nadie la consumía; los tests no la mencionaban.

**Detección:** mi propio *spot-check* contra el catálogo real, no los tests del worker. Los 45
tests del registro pasaban.

**Resolución:** `ModelInfo.excluded`, reflejado desde la ficha, sin sacar el modelo del
inventario (el dashboard debe poder mostrarlo). Test de regresión añadido.

**Lección:** los tests verdes no prueban que se lea toda la configuración; prueban que se lee la
que alguien recordó probar.

### 5.9 Cuando no se observó RED, se dice

**Síntoma:** en dos unidades (adaptador de transporte y adaptador de razonamiento) escribí los
tests y la implementación **antes** de ejecutar. No hubo RED observado.

**Resolución:** en vez de narrar un TDD que no ocurrió, se aplicó **mutación**: se rompió el
reintento en 429 y el límite del semáforo, y se comprobó que fallaban exactamente los dos tests
previstos; restaurado el código, verde de nuevo. Queda escrito en el documento de la feature,
con la tabla de mutaciones.

**Lección:** la evidencia honesta vale más que la evidencia bonita. Y un test que nunca falló no
se sabe si sirve; la mutación lo demuestra.

### 5.10 Errores menores, todos reales

| Error | Detección | Resolución |
|---|---|---|
| `.pyc` versionados en el primer commit | `git show --stat` | `git rm --cached` + `.gitignore` + `--amend` |
| Literales de test tomados de una sonda anterior, no de la fixture guardada | el test falló al correr | valores de la fixture, fijados como literales |
| `pyright` marcó 1 error y commiteé igual | la propia salida del comando, visible | `--amend` con `Mapping` en vez de `dict` (invarianza) |
| Mi sonda usó `base_url`, `candidates`, `supports_tools` inexistentes | `TypeError`/`AttributeError` inmediatos | leer la firma real antes de invocar |
| Un test mío sobre-especificaba el desempate por escalón | el test falló con razón | reformulado para probar lo que importa |
| El test de valores por defecto se rompió al aparecer un `.env` real | suite en rojo | `monkeypatch.chdir(tmp_path)` para aislarlo |
| `uv build` exigía el módulo con el nombre del proyecto | `uv sync` falló | `[tool.uv.build-backend] module-name` |
| Un worker devolvió informe vacío (NAU-13) | notificación de fallo | se descarta su trabajo (árbol limpio) y se reintenta |

---

## 6. Decisiones y desviaciones justificadas

| Decisión | Justificación |
|---|---|
| Refresco del registro **perezoso con TTL** en vez de "al arrancar" | el diseño dice "se construye al arrancar"; hacerlo así evita que el scaffold y el modo `mock` hagan red al importar, y mantiene el *fail-open* al inventario de la ficha. Desviación anotada en el documento de feature |
| No exponer el eco del mensaje del servidor en errores | el valor de depuración no compensa un vector de fuga de credenciales |
| Penalizar `always_on`/`effort`/`unknown` cuando se pide `off` | pedir algo que no se puede cumplir es pagar latencia creyendo que se ahorró; medido, no supuesto |
| Tests que cargan la ficha real | un test que copia la suposición equivocada verifica la suposición, no el sistema |
| Constantes de política marcadas `[HIPÓTESIS]` | el propio diseño lo pide: la matriz de calidad medida (NAU-14) las sustituye |
| `deepseek-v4-flash` no se excluye nunca | es el baseline R0; se penaliza cuando se pide `off`, no se prohíbe |

---

## 7. Verificación

Todo lo que sigue está **observado**, no narrado:

| Qué | Resultado |
|---|---|
| Suite del servidor | **231 passed** |
| Lint y tipos | `ruff` limpio · `pyright` 0 errores, 0 warnings |
| Contrato de Clef | 3 fixtures reales, parser con 9 tests |
| Catálogo real | 18 → 12 servibles; 6 excluidos con motivo |
| Gateway HTTP real | ruteo + cabeceras `x-nan-auto-*` + 50 eventos SSE |
| Clave del cliente | canario en 7 escenarios; verificador independiente con su propio canario y barrido de `gc.get_objects()` |
| Privacidad del extractor | canario en system, usuario, imagen y audio: **no aparece en la salida** |
| Razonamiento | 0 tokens en tarea trivial (antes: 72, con `content: null`) |
| Verificación independiente | NAU-02: 3 claims VERIFIED + 1 REFUTED → corregido. NAU-07/08: 5 VERIFIED, 0 bloqueadores |

---

## 8. Pendiente y bloqueado

**Bloqueado por decisión humana:**

- **Revisión nativa RDD no disponible** en este clon: `package-local-binary-missing`. Se arregla
  con `node scripts/install-gentle-ai.mjs` dentro del paquete `gentle-pi`, fuera del proyecto.
  Es la única pieza del método del diseño que no está operativa.
- **Protecciones de GitHub** (capa 1): PR obligatorio, CI, bloqueo de force push. El hook local
  (capa 2) sí está activo.

**Pendiente técnico:**

- NAU-10: fallback y circuit breaker (alto riesgo).
- NAU-11: presupuesto de decisión de 800 ms con falla abierta. Con Clef medido en 450–570 ms, el
  margen es estrecho: conviene decidir si sube el presupuesto o si se acepta más fallback.
- NAU-12: estabilidad por conversación.
- NAU-13: log de decisiones sin contenido (primer intento del worker falló sin informe).
- NAU-14/15: matriz de calidad y evaluación offline — es lo que convierte las `[HIPÓTESIS]` de la
  política en datos.
- NAU-16/17: dashboard.
- Tres tests de regresión sugeridos por el verificador (canario en passthrough y en 401/429).

---

## 9. Método: lo que este proyecto enseña

1. **Probar contra el mundo real, no contra la documentación.** Tres de los errores más caros
   (razonamiento, catálogo, `mode: chat` mentiroso) solo aparecieron llamando a la API.
2. **El verificador debe intentar refutar.** Las dos rondas encontraron cuatro defectos reales
   que el autor no habría visto.
3. **La seguridad necesita tests adversarios.** Probar el caso correcto no prueba nada.
4. **Una suposición copiada en un test se convierte en un test inútil.**
5. **`bool` es un `int`; `on` es un `true`; y un valor por defecto puede ocultar un secreto
   vacío.** Los tres son trampas de plataforma, no de lógica.
6. **Decir "no observé RED" y demostrarlo con mutación** es más útil que fingir un TDD que no
   pasó.
