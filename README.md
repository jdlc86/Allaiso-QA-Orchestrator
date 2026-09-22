# Allaiso-QA-Orchestrator

Vendor-neutral control plane for AI-orchestrated end-to-end testing on a real workstation.

## Purpose

This repository is **not tied to one application or one ChatGPT conversation**. It is a shared protocol between:

1. an **Orchestrator AI** (for example a ChatGPT session),
2. GitHub as the durable/versioned **control plane**,
3. a **local execution agent** running on the QA workstation (initial target: OpenCloud),
4. one or more **Applications Under Test (AUTs)**.

An orchestrator publishes a self-contained test job. The local agent claims it, drives the browser/computer, records evidence, and publishes a structured result. A later/different AI session can read the same repository and continue without hidden conversational state.

## Non-negotiable architecture rules

- GitHub is the source of truth; chat history is not.
- Jobs are immutable once claimed. Results never overwrite jobs.
- Every job has a globally unique `job_id`, `project_id`, and `run_id`.
- Project-specific credentials are never committed. Use local secret storage / GitHub secrets as appropriate.
- The local agent must only execute jobs explicitly allowed by its policy.
- Parallel projects/sessions must not share mutable state or browser profiles.
- Destructive actions require an explicit job policy and must be limited to designated test environments.
- A test may fail; the executor must report failure rather than silently changing the AUT to make the test pass.

## Start here

### If you are the AI/operator on the QA computer
Read, in order:

1. `docs/OPENCloud-BOOTSTRAP.md`
2. `docs/ARCHITECTURE.md`
3. `docs/PROTOCOL.md`
4. `config/runner.example.yaml`

Then inspect the workstation, install only missing prerequisites, register a dedicated GitHub Actions self-hosted runner following GitHub's current repository-generated instructions, verify browser automation/capture capabilities, and run `examples/jobs/smoke-demo.yaml` only against a harmless test target.

### If you are an Orchestrator AI (ChatGPT or another AI)
Read:

1. `AGENTS.md`
2. `docs/PROTOCOL.md`
3. `schemas/job.schema.json`
4. `schemas/result.schema.json`

Then create jobs according to the protocol. Do not assume knowledge that exists only in the current chat.

## Repository layout

```text
AGENTS.md                       AI-to-AI operating contract
README.md                       Entry point
docs/
  ARCHITECTURE.md               Components, boundaries and multi-project design
  OPENCloud-BOOTSTRAP.md        Instructions for the local OpenCloud AI
  PROTOCOL.md                   Job/result lifecycle and communication contract
config/
  runner.example.yaml           Local executor configuration template
schemas/
  job.schema.json               Machine-readable job contract
  result.schema.json            Machine-readable result contract
examples/jobs/
  smoke-demo.yaml               Minimal example job
projects/
  README.md                     How AUT profiles are registered
jobs/                           Orchestrator-created work items
results/                        Executor-created structured results
evidence/                       Screenshots/logs/traces grouped by run
scripts/                        Bootstrap/executor implementation (next milestone)
.github/workflows/              Dispatch/validation workflows (next milestone)
```

## High-level flow

```text
ChatGPT session A ─┐
ChatGPT session B ─┼─> GitHub control plane ─> QA workstation / OpenCloud ─> AUT A
Other AI          ─┘              ^                         └───────────────> AUT B
                                  |
                         results + evidence
```

GitHub is deliberately asynchronous. The orchestrator should normally send a meaningful test bundle, not individual mouse clicks. The executor can make bounded diagnostic decisions described in the job, then return a complete report.

## Known application links

- **GestionPisos / Allaiso** → `projects/gestionpisos/README.md` → **REGISTERED_NOT_ACTIVE**.
- Product repository: https://github.com/jdlc86/gestionpisos

This registration is a durable cross-repository link only. It does not mean that a runner, browser session, credentials, or validated execution path already exists.

## Status

Phase 0: architecture and protocol bootstrap. No workstation software should be considered production-ready until the bootstrap handshake and a harmless smoke test have passed.

The GestionPisos link is currently documentation-only. Operational activation must be explicit and reflected in both repositories before any session treats this platform as an available E2E executor.
