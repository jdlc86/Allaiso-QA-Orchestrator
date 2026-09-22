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

Este repositorio conserva la autoridad sobre la futura infraestructura de orquestación QA cuando se implemente.

## Estado actual

A 22/09/2026 el runner `allaiso-qa-JDIA`, el executor versionado y el control de navegador mediante OpenClaw están validados. El handshake real pasó en el run `35787573517` y el smoke público de GestionPisos pasó en el run `35787573540`, ambos sobre `main` en `4c8b9ebd7eac2ebb65680ca737ac39c96c350e1c`. El smoke verificó navegación real, lectura semántica de la pantalla de acceso y captura de evidencia, sin autenticación ni mutaciones.

La configuración no secreta está en `projects/gestionpisos/project.yaml`. La integración queda operativa únicamente para el smoke público de solo lectura. Las pruebas autenticadas y cualquier escritura sobre el AUT siguen desactivadas hasta una activación explícita posterior.

## Objetivo previsto

Servir como punto de integración futuro para pruebas E2E masivas, paralelas, repetibles y con evidencia suficiente para que otra sesión o agente pueda reconstruir cada ejecución.

