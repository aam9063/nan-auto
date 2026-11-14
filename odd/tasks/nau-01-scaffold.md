# NAU-01: estructura del repo y CI

## Especificación (literal)

> "Vamos a empezar el proyecto. Dentro tienes un md para que lo leas y veas en que consiste. Crea toda la estrructura del proyecto y dime cuando creo el .env para poner la api key y asi ya puedas trabajar en automatico. Tienes total libertad para pushear y mergear a dev.Todo lo dejaras en dev hasta que yo abra pr a main. Sube todas las ramas al repo y sigue buenas practicas de git"

Del spec (`nan-auto.md` secciones 13, 14, 15):

- NAU-01: "Estructura del repo (`server/` con uv, `web/` con pnpm) y CI (ruff, pyright, pytest, lint de la web)". Criterio: "`uv run pytest` verde en CI sobre PR a `dev`".
- Estructura objetivo (sección 13): `server/src/nan_auto/{gateway,registry,router_core,clef_client,schema}`, `server/tests/fixtures/clef/`, `web/`, `eval/`, `config/`, `odd/tasks/`, `docs/`, `.githooks/`, plantillas `.github/`.
- Variables de entorno: `CLEF_PROVIDER`, `NAN_KEY`, `NAN_BASE_URL`, `DEFAULT_MODEL`, `DECISION_TIMEOUT_MS`, `CLEF_MAX_IN_FLIGHT`.

## Objetivo

Repo inicializado con la estructura completa del spec, CI mínima que verifica (ruff, pyright, pytest, web lint/typecheck/build), hooks que impiden push directo a `main`/`dev`, y `.env` local listo para la clave de NaN.

## Problema y motivo

Sin estructura ni CI no hay base para ODD: cada tarea necesita rama desde `dev`, commits de unidad y verificación reproducible. El `.env` desbloquea el modo automático con la API key del usuario.

## Alcance

- Dentro: scaffold de `server/` (uv, FastAPI, tests), `web/` (Vite React TS + Tailwind v4), `eval/`, `config/`, `odd/tasks/`, `docs/`, `.githooks/`, `.github/`, `.env` + `.env.example`, CI, CODEOWNERS, plantilla de PR, README.
- Fuera: implementación de NAU-02 en adelante; nada del pipeline del router.

## Restricciones

- Todo entra a `dev` vía merge de rama `chore/*` (el usuario autorizó explícitamente push y merge a `dev`; nunca a `main`).
- Conventional Commits; tests en el mismo commit que el comportamiento.
- `.env` nunca versionado.

## Configuración de pruebas

- Modo TDD: activado para `server` (runner `uv run pytest`); el scaffold es estructural, se verifica con el smoke test del doc de feature.
- Fuente de la decisión: sección 15.3 del spec.

## Entrega

- Estrategia: single-pr (rama `chore/bootstrap` → merge --no-ff a `dev`).
- Líneas previstas: ~600 (mayoría generados por scaffolds).
- Slices: un único PR/slice; se fusiona a `dev` directamente (autorizado por el propietario).

## Tareas

- [x] NAU-01-T1 Commit inicial del spec en `main` y creación de `dev`. Aceptación: `git ls-remote` muestra `main` y `dev`. Riesgo RDD: bajo
- [ ] NAU-01-T2 Documento de feature. Aceptación: este fichero existe y está versionado. Riesgo RDD: bajo
- [ ] NAU-01-T3 Scaffold `server/` con uv + pytest verde en local. Aceptación: `uv run pytest` sale 0. Riesgo RDD: bajo
- [ ] NAU-01-T4 Scaffold `web/` con Vite + Tailwind + build verde. Aceptación: `pnpm --dir web build` sale 0. Riesgo RDD: bajo
- [ ] NAU-01-T5 Carpetas `eval/`, `config/`, `odd/tasks/`, `docs/` con contenido base. Aceptación: ESTRUCTURA.md refleja el árbol del spec. Riesgo RDD: bajo
- [ ] NAU-01-T6 Git hygiene: `.githooks/pre-push`, `core.hooksPath`, CI, plantilla PR, CODEOWNERS, `.gitignore`. Aceptación: hook activo y `ci.yml` coincide con los runners locales. Riesgo RDD: bajo
- [ ] NAU-01-T7 `.env` local + `.env.example`. Aceptación: `.env` presente y no aparece en `git status`. Riesgo RDD: bajo
- [ ] NAU-01-T8 Verificación completa (pytest, ruff, pyright, web build). Aceptación: todos en verde. Riesgo RDD: bajo
- [ ] NAU-01-T9 Push de ramas y merge a `dev`. Aceptación: `origin/dev` contiene el merge. Riesgo RDD: bajo

## Evidencia de verificación

| Tarea | Prueba | Resultado | Commit |
|---|---|---|---|
| T1 | `git push origin main dev` | OK (06d8263) | 06d8263 |

## Revisión nativa (RDD)

| Tarea | Nivel | Resultado |
|---|---|---|
| T3–T9 | bajo | pendiente de assess al cerrar el slice |

## Progreso

T1 hecho, resto en curso.

## Siguiente paso

T3: scaffold del backend.
