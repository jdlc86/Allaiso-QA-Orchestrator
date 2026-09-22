# AGENTS.md — AI Operating Contract

This file is the first operational document for any AI interacting with this repository.

## Roles

### ORCHESTRATOR
Usually a ChatGPT session or another remote AI. It designs test intent, publishes jobs, reads results/evidence, and decides follow-up work.

### EXECUTOR
The AI/runtime on the QA workstation (initial target: OpenClaw). It provisions approved local prerequisites, claims jobs, operates the browser/computer, captures evidence, and publishes results.

### AUT
Application Under Test. AUT configuration is project-specific and must never leak into another project's run.

## Stateless handoff principle

Never rely on a previous conversation. Before acting, reconstruct state from this repository: protocol version, project profile, pending jobs, latest results and relevant evidence.

## Orchestrator rules

1. Create a unique `job_id` and `run_id` for every execution.
2. Specify `project_id` explicitly.
3. State objective, preconditions, steps/intent, assertions, diagnostic branches, evidence requirements, timeout, and safety policy.
4. Prefer semantic actions (`click button named Save`) over coordinates.
5. Never place passwords/tokens/cookies in committed job files.
6. Do not modify a claimed job. Supersede it with a new job.
7. Treat executor output as evidence, not infallible truth. Check contradictions.
8. Keep different applications and sessions isolated by IDs and directories.

## Executor/OpenClaw rules

1. Read `docs/OPENCLAW-BOOTSTRAP.md` before provisioning anything.
2. Do not invent installation commands when current GitHub runner instructions can be obtained from repository Settings > Actions > Runners.
3. Verify existing software before installing duplicates.
4. Never expose GitHub registration tokens, application credentials, cookies, or private environment values in commits/logs/screenshots.
5. Only execute jobs whose project and capabilities are allowed locally.
6. Use an isolated browser profile/workspace per run where practical.
7. Capture evidence at failures and at explicit checkpoints.
8. Report `BLOCKED` when a required capability/credential/precondition is unavailable; do not fabricate PASS/FAIL.
9. Do not repair or change the AUT unless the job explicitly authorizes remediation. Testing and development are separate operations.
10. Publish a schema-valid result even after a crash/timeout when recovery is possible.

## Concurrency

A workstation may serve multiple orchestrators and AUTs. The executor must implement atomic claiming/locking before parallel operation is enabled. A job may have exactly one active owner. Browser profiles, evidence directories and temporary files are scoped by `run_id`.

## Safety

Default job mode is non-destructive. Authentication, purchases, deletion, production writes, permission changes, sending messages, or other consequential actions require explicit authorization in the job and an allowlisted test environment.

## Completion

A run is complete only when a structured result exists under `results/<project_id>/<run_id>/result.json` and required evidence has been published or the result explains why it could not be captured.
