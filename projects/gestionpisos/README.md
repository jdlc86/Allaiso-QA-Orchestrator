# GestionPisos / Allaiso

## Registro lógico

- Project/AUT: **GestionPisos / Allaiso**
- Repositorio de producto: https://github.com/jdlc86/gestionpisos
- Repositorio de orquestación QA: https://github.com/jdlc86/Allaiso-QA-Orchestrator
- Estado de integración: **PUBLIC_SMOKE_VERIFIED**

Este registro existe para que cualquier sesión futura descubra la relación entre ambos repositorios sin depender de memoria conversacional.

## Autoridad

GestionPisos conserva la autoridad sobre:

- contratos de producto;
- seguridad y permisos;
- datos;
- workflows;
- criterios de aceptación;
- Gates E2E.

Este repositorio conserva la autoridad sobre la infraestructura de orquestación QA.

## Estado actual

A 23/09/2026 el runner `allaiso-qa-JDIA`, el executor versionado y el control de navegador mediante OpenClaw están validados en el nodo Windows real.

Bootstrap automático verificado sobre `main` en `3db8b882afc45d631204e133fc5a796c7ce920d3`:

- GestionPisos Public Smoke #15 — run `35806072819`: **SUCCESS**.
- QA Node Handshake #16 — run `35806072832`: **SUCCESS**.

El executor arranca/reutiliza el Gateway OpenClaw de forma idempotente antes de preparar el navegador. El usuario no necesita arrancar manualmente el Gateway para una ejecución normal.

El smoke público verificó navegación real, lectura semántica de la pantalla de acceso y captura de evidencia con Chrome real. El artifact del Public Smoke #14 (`10728011344`) contiene una captura confirmada de la pantalla de acceso de Allaiso.

La configuración no secreta está en `projects/gestionpisos/project.yaml`. La integración sigue operativa únicamente para el smoke público de solo lectura. Las pruebas autenticadas y cualquier escritura sobre el AUT continúan desactivadas hasta una activación explícita posterior.

## Siguiente etapa

El siguiente bloque debe habilitar autenticación QA de forma incremental y segura:

1. definir el origen y ciclo de vida de credenciales sin guardar valores en Git;
2. comprobar cómo introducir secretos en OpenClaw sin exponerlos en argv, logs o evidencia;
3. habilitar una sesión autenticada **read-only**;
4. validar una prueba mínima autenticada;
5. solo después diseñar escritura controlada sobre fixtures autorizados.

No se debe pasar directamente a Gate 1.1 mientras estos controles no estén implementados y verificados.

## Objetivo previsto

Servir como punto de integración para pruebas E2E masivas, repetibles y con evidencia suficiente para que otra sesión o agente pueda reconstruir cada ejecución.
