# Authenticated QA Security Design

## Purpose

Define the activation path from the already verified public/read-only OpenClaw smoke to authenticated GestionPisos / Allaiso E2E testing without placing credentials in Git, jobs, logs, screenshots or process arguments.

This document is a security and execution design. It does **not** activate authentication or AUT writes by itself.

## Current baseline

Verified before this design:

- self-hosted Windows runner executes only approved workflows;
- OpenClaw Gateway can be started/reused automatically by the executor;
- the dedicated public browser profile can open GestionPisos, inspect semantic UI and capture evidence;
- public smoke and node handshake pass on the real QA workstation;
- authentication and AUT writes remain disabled.

## Security principles

1. A committed job contains secret **references or requirements**, never secret values.
2. A browser profile used for authentication is separate from the public-smoke profile.
3. A read-only authenticated run must be possible before any write-capable run exists.
4. A secret must not be passed in an OpenClaw command-line argument unless the installed OpenClaw version provides a proven mechanism that prevents exposure in process listings and diagnostics. Until then, command-line secret injection is prohibited.
5. Authentication evidence starts only after credential entry is complete. Screenshots of filled login/MFA forms are prohibited.
6. Session expiry or unavailable credentials are normally `BLOCKED`, not an AUT `FAILED`.
7. Write-capable jobs must be explicitly allowlisted to designated fixtures and action families.
8. The self-hosted runner must not run authenticated jobs from untrusted pull requests or forks.

## Activation stages

### B0 — capability discovery, no secrets

Before implementing credential automation, inspect the actually installed OpenClaw 2026.5.12 command surface on the QA node.

Required questions:

- Which semantic click/type/fill commands are available?
- Can sensitive text be supplied through stdin, a protected temporary file, an environment reference, or another non-argv mechanism?
- Can the executor suppress/redact command diagnostics for sensitive actions?
- Can the browser profile be selected explicitly for every command?
- Can the executor detect current URL and semantic authenticated state without taking a screenshot?

No credential value is introduced during this stage.

If OpenClaw does not expose a safe secret-input path, automated credential entry remains disabled.

### B1 — authenticated session reuse, read-only

The first authenticated mode should avoid automated password/TOTP handling entirely.

Use a dedicated local OpenClaw/Chrome profile:

- profile: `qa-gestionpisos-auth`
- proposed CDP port: `18892`
- project scope: `gestionpisos`
- purpose: authenticated, read-only QA only

The profile is provisioned once on the QA workstation and authenticated interactively when required. The persisted browser session remains local to that profile; cookies/session storage are never committed or uploaded as artifacts.

Initial authenticated workflow requirements:

- manual dispatch only;
- no AUT writes;
- no destructive actions;
- serialized execution on the existing QA runner;
- navigate only to the GestionPisos allowlisted URL prefix;
- assert an authenticated-only semantic element or route;
- capture evidence only after confirming the login form is no longer visible;
- if the session is expired, return `BLOCKED` with a non-secret diagnostic.

This stage gives the orchestrator a useful authenticated read-only capability before introducing credential automation.

### B2 — credential-based login automation

Only activate after B0 proves a safe input path.

Credential values should live in one approved secret source, initially one of:

- GitHub Actions environment/repository secrets available only to the authenticated workflow; or
- a protected local secret store on the QA workstation.

The project manifest may list secret **names**, never values.

Proposed logical names, subject to implementation review:

- `ALLAISO_QA_EMAIL`
- `ALLAISO_QA_PASSWORD`
- `ALLAISO_QA_TOTP_SEED` only if automated MFA is explicitly approved later

Rules:

- never serialize values into the job JSON/YAML;
- never echo values;
- never place values in artifact files;
- never place values in OpenClaw argv;
- do not rely on GitHub log masking as the primary protection;
- clear protected temporary material immediately after use;
- stop evidence capture while credentials/MFA are being entered.

A dedicated QA account should have the minimum role and data access required for the scenarios it is intended to test.

### C — controlled writes on fixtures

Authenticated read-only verification must pass before enabling writes.

Write-capable execution must add a separate authorization layer with at least:

- explicit `write_scope = fixtures_only` or equivalent;
- allowlisted project/environment;
- allowlisted action families;
- allowlisted fixture identifiers;
- idempotency/request keys where supported by the AUT;
- precondition checks proving the target is the intended fixture;
- post-action evidence and structured assertions;
- no arbitrary navigation followed by generic click/type authority.

For GestionPisos the initial fixture target is expected to be the designated QA property/occupancy set, but fixture IDs must be versioned in project configuration before write activation.

Destructive actions such as permanent deletion, account/permission changes, bulk operations or non-fixture writes remain disabled until separately approved.

## Profile isolation

The existing public profile must stay unchanged:

- `qa-gestionpisos-public` / CDP 18891

Authenticated state must use a distinct profile:

- `qa-gestionpisos-auth` / proposed CDP 18892

A later write-enabled profile may be separated again if the permission model or test account warrants it. Do not reuse the demo profile or another project's profile.

## Result classification

Use these semantics for authenticated runs:

- `PASSED`: authenticated precondition is valid and all requested read-only assertions pass.
- `FAILED`: the authenticated session is valid, but observable AUT behavior contradicts a required assertion.
- `BLOCKED`: authentication/session/credential/MFA prerequisite is unavailable or expired before a valid test can begin.
- `ERROR`: runner/OpenClaw/browser/executor malfunction prevents a valid determination.

Do not classify an expired session as an application defect.

## Evidence policy

Authenticated evidence may contain personal or tenant data. Therefore:

- capture only what is needed for the assertion;
- never capture credentials, TOTP values or recovery material;
- prefer semantic text observations over screenshots when sufficient;
- do not upload browser profile directories, cookies, local/session storage or raw credential-bearing traces;
- redact known token/JWT patterns in diagnostics;
- add further redaction before network/console evidence is enabled.

## First implementation sequence

1. Run B0 capability discovery against the installed OpenClaw version.
2. Add project-scoped authenticated browser-profile policy, but keep it disabled by default.
3. Add a manual, read-only authenticated-session check using `qa-gestionpisos-auth`.
4. Provision/authenticate that profile once on the workstation.
5. Verify the authenticated read-only job returns structured PASS/BLOCKED correctly and leaks no secret/session material.
6. Only then decide whether B2 credential automation is necessary immediately or whether session reuse is sufficient for the next regression gates.
7. Design fixture-scoped writes separately before Gate 1.1.

## Explicit non-goals for the first authenticated block

The first block must **not**:

- automate Gate 1.1;
- enable arbitrary typing/clicking for authenticated jobs;
- add passwords/TOTP to repository files;
- upload authenticated browser state;
- weaken GestionPisos RLS or create a QA bypass in the AUT;
- turn the executor into a general remote-control shell;
- run authenticated workflows automatically on pull requests.
