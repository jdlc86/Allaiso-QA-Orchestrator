# GestionPisos / Allaiso

## Registro lógico

- Project/AUT: **GestionPisos / Allaiso**
- Repositorio de producto: https://github.com/jdlc86/gestionpisos
- Repositorio de orquestación QA: https://github.com/jdlc86/Allaiso-QA-Orchestrator
- Estado de integración: **PUBLIC_SMOKE_PENDING**

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

A 22/09/2026 el runner `allaiso-qa-JDIA`, el executor versionado y el control de navegador mediante OpenClaw han sido validados con el proyecto `demo`. GestionPisos entra ahora en activación controlada mediante un smoke público, sin autenticación ni mutaciones.

La configuración no secreta está en `projects/gestionpisos/project.yaml`. No se considerará la integración operativa para pruebas autenticadas hasta que el smoke público termine en PASS y exista una activación explícita posterior.

## Objetivo previsto

Servir como punto de integración futuro para pruebas E2E masivas, paralelas, repetibles y con evidencia suficiente para que otra sesión o agente pueda reconstruir cada ejecución.

