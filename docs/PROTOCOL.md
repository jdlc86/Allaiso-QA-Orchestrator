# QA Exchange Protocol v0.1

## Principle

The protocol is independent of ChatGPT, OpenCloud and any particular AUT.

## Job location

`jobs/<project_id>/<job_id>.yaml`

A job contains:
- protocol version;
- immutable IDs;
- project/AUT identity;
- requesting orchestrator;
- objective;
- environment and preconditions;
- ordered actions or high-level scenario;
- assertions;
- bounded diagnostic branches;
- evidence requirements;
- safety/side-effect policy;
- timeout/retry policy.

## Result location

`results/<project_id>/<run_id>/result.json`

A result contains:
- IDs and executor identity;
- timestamps/duration;
- terminal status;
- per-step observations;
- assertion outcomes;
- diagnostics;
- evidence references;
- redacted error details;
- executor summary.

## Evidence location

`evidence/<project_id>/<run_id>/...`

Do not commit raw secrets or screenshots known to contain credentials/personal data. The executor must redact or omit them and explain the omission in the result.

## Claiming

The production executor must claim a job atomically before execution. The exact GitHub primitive (dispatch + concurrency group, claim file, issue/commit status, or another safe lock) is intentionally left to the implementation milestone. Until this is implemented, run only one executor/job at a time.

## Orchestrator behavior

An orchestrator reads results after completion and may create a **new** follow-up job. It must never rewrite historical results to fit expectations.

## Executor autonomy

The executor may perform only bounded diagnostic actions authorized by `diagnostics`. Example: after an assertion fails, capture screenshot + console + visible validation errors and stop. It may not wander through unrelated application areas.

## Status semantics

- `PASSED`: all required assertions satisfied.
- `FAILED`: AUT behavior contradicted at least one required assertion.
- `BLOCKED`: test could not validly run because a prerequisite/capability/credential/environment was unavailable.
- `ERROR`: executor/infrastructure malfunction prevented a valid determination.
- `CANCELLED`: explicitly cancelled before a valid determination.

A timeout is normally `ERROR` unless the assertion itself is explicitly about a timeout/response deadline.

## Versioning

Jobs/results declare `protocol_version`. Breaking schema changes increment the major version. Executors must reject unsupported major versions as `BLOCKED` rather than guessing.
