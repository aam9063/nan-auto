# NAU-19: lavado de cara del dashboard

## Especificación (literal)

> «vale quiero que le des un buen lavado de cara al front, aqui te dejo el diseño [5 imágenes].
> usa esos colores y letra Inter. Adaptalo obviamente a como esta el proyecto ahora porque esto es
> del brief inicial. Recuerda: rama y despues pr y merge a dev, pero antes de hacer pr y merge
> quiero verlo y ver si hay que modificar algo»

## Objetivo

Restyling completo del dashboard web a tema claro con la identidad visual de los mockups: sidebar
blanca, acento verde azulado profundo, tipografía Inter + monospace para métricas e ids, tarjetas
blancas con borde sutil, chips de tier/estado. Las 4 pantallas existentes adaptadas: el diseño
incluye una quinta («Comparación») que no existe en el backend y no se implementa.

## Entrega

- Rama `feature/nau-19-ui-refresh` (basada en dev). **Sin PR ni merge**: el humano revisa primero.
- Sin commit hasta que el humano apruebe lo que ve (instrucción del L1: «quiero verlo»).

## Tareas

- [x] NAU-19-T1 Tokens de diseño (tema claro, acento teal, Inter) + sidebar + shell de App.
      Riesgo RDD: bajo
- [x] NAU-19-T2 Playground rediseñado (dos columnas: formulario / decisión+Clef+candidatos+respuesta).
      Riesgo RDD: bajo
- [x] NAU-19-T3 Feed en vivo y Registro con el estilo del mockup (KPIs, tabla, panel de detalle).
      Riesgo RDD: bajo
- [x] NAU-19-T4 Evaluación con KPIs, dispersión calidad-latencia y baselines desde report.json;
      honesto con lo que no hay datos (sin matriz de confusión). Riesgo RDD: bajo

## Restricciones

- Copys en español (como los mockups); datos de API sin traducir.
- Sin inventar endpoints ni datos: lo que el backend no da, estado vacío honesto.
- Sin librerías nuevas salvo la fuente (@fontsource-variable/inter).

## Evidencia de verificación

- `pnpm install` (en `web/`): OK — añadido `@fontsource-variable/inter 5.3.0` y lockfile actualizado.
  (Nota: `npm install` falla en este repo con `EUNSUPPORTEDPROTOCOL "workspace:"` incluso en la base;
  se usó `pnpm`, el packageManager declarado en `web/package.json`.)
- `pnpm run typecheck` (web): OK, exit 0.
- `pnpm run lint` (web, oxlint): OK, exit 0 (1 aviso preexistente en `useRegistry.ts`, sin cambios en
  ese fichero).
- `pnpm run build` (web): OK, exit 0 — `tsc -b` + `vite build`, 29 módulos, Inter empaquetada.
- Sin commit: se deja todo sin stage para revisión visual del humano.

## Log

- **L1** (2026-11-14, usuario): petición original (verbatim arriba).
- **L2**: rama creada desde dev; el PR/merge queda bloqueado hasta aprobación visual del humano.
- **L3** (2026-11-14): restyling completo aplicado (T1–T4), sin cambios de API ni features nuevas.
  Por pantalla:
  - **Shell/T1**: tema claro con tokens en `web/src/index.css` (`@theme`: canvas `#f2f4f1`, surface
    blanca, línea `#e4e8e3`, acento `#0d7a5f`, accent-soft `#dcece4`, ink/body/muted/faint), Inter
    Variable en `main.tsx`, stack mono (JetBrains Mono → ui-monospace) para ids/métricas. Header
    oscuro eliminado: sidebar 250px con logo redondeado teal + icono de nodos, nav con iconos y
    `aria-current`, footer con "Clef X,X s" (última decisión, poll cada 10 s) y "Gateway en
    línea/sin conexión". Breadcrumb "Gateway · nan-auto" + título por pantalla. La API key se movió
    al formulario del Playground (sigue solo en memoria). Sondeo de salud vía `/v1/decisions?limit=1`
    porque el proxy de Vite solo reenvía `/v1` y `vite.config.ts` no era editable; fila "En vuelo"
    omitida (la app no trackea peticiones en vuelo).
  - **Playground/T2**: dos columnas (40/60). Izquierda: tarjeta "Petición" (Mensaje, control
    segmentado de perfil con ids en mono, API key + nota de memoria, botones relleno "Decidir
    modelo" / contorno "Enviar y ver la respuesta", mismos handlers) y "Pruebas rápidas" (Saludo,
    Bug difícil, Problema de mates). Derecha: "Modelo elegido" (id grande mono, chip de escalón vía
    `/v1/registry`, chip de razonamiento, reason en franja de código gris — usa la cabecera
    `x-nan-auto-reason` del envío si existe), "Lo que dijo Clef" (ProbabilityBars teal),
    "Candidatos" (tabla; puntuación "—" porque el decide no la expone, estados Elegido/Descartado:
    razón/Considerado), "Respuesta" (chips HTTP/total/tokens en mono, cabeceras x-nan-auto-*,
    respuesta en bloque gris). Estados de carga (skeleton) y error (rojo) honestos.
  - **Feed/T3**: fila de 4 KPIs (Peticiones por minuto sobre la ventana del último registro, Decisión
    p50/p95 de clef_latency_ms, Con fallback %, Total en el registro — "Premium evitados" no es
    computable con los datos actuales y se sustituyó según lo previsto en la especificación).
    Tarjeta "Peticiones" con chip "En directo" (teal) + punto, tabla con filas seleccionables
    (teclado incluido) y resalte accent-soft. Panel "Petición seleccionada": id, timestamp,
    respuestas de Clef con barras teal (la anchura muestra la confianza, no la distribución — el log
    solo guarda etiqueta+confianza), valores x-nan-auto-* en mono. Nota explicativa en tarjeta
    clara. No se añadieron controles de pausa/filtro porque no existían; SSE intacto.
  - **Registro/T3**: tarjeta "Modelos de NaN" + botón contorno "Refrescar registro", tabla
    Modelo/Escalón (chip por tier T1 azul, T2 teal, T3 violeta, premium ámbar)/Contexto/Entrada/
    Herramientas/Razonamiento/Estado (chip con punto)/Circuito (abierto hasta X/cerrado)/Latencia
    ("sin dato" honesto). Tarjeta "Cómo se lee" con Circuito / Sin cuota (402) / Herramientas en XML.
  - **Evaluación/T4**: KPIs desde `eval/data/report.json` (NAU-14): Retención de calidad (R3 vs
    mejor modelo único), Reducción de latencia (R3 vs R1), Cobertura, Costo de decisión (latencia
    media de Clef de `/v1/decisions`, "sin dato" si el gateway no responde). Dispersión SVG inline
    (puntos huecos por modelo + router R3 teal + guía discontinua), tabla Baselines R0–R3 con
    políticas descritas, nota de informe incompleto y nota de matriz de confusión pendiente. El
    informe se incrusta como snapshot tipado en `web/src/evalReport.ts` (generado del report.json:
    el gateway no sirve ningún endpoint con medidas de calidad y `tsconfig.json`/`vite.config.ts`
    quedaban fuera de las superficies editables); hay que regenerarlo si se re-ejecuta la eval.
