# OpenClaw Workstation Bootstrap

> Instruction to the local AI: treat this repository as the source of truth. Your task is to prepare this computer as a **generic QA execution node**, not as a node dedicated to one application.

## 0. Safety gate

Before changing the computer:

1. identify OS/architecture and current user privileges;
2. inventory existing Git, GitHub CLI (optional), Node/Python/browser tooling, OpenClaw, and any browser automation support;
3. report what is already installed and what is missing;
4. do not uninstall/upgrade unrelated software merely to satisfy this project;
5. never print or commit credentials/tokens.

If elevated/admin privileges are required, explain exactly why before requesting them.

## 1. Clone this repository

Use the repository URL supplied by the operator. Work from a dedicated local directory. Pull protocol/documentation updates before provisioning or executing jobs.

## 2. GitHub self-hosted runner

The target design uses a **repository-scoped GitHub Actions self-hosted runner**.

Do **not** copy a static registration token or hard-code runner download URLs from this document. Registration tokens expire and runner releases change.

Open the repository in GitHub and use:

`Settings -> Actions -> Runners -> New self-hosted runner`

Select the workstation OS/architecture and follow the commands GitHub generates at that moment. Use a recognizable runner name such as `allaiso-qa-<machine>` and ensure the dedicated label `allaiso-qa` is present. Prefer installing the runner as a service only after the interactive smoke test works.

Security requirement: this runner must not execute workflows originating from untrusted forks/pull requests. Treat workflow write access as workstation code-execution access.

## 3. Required local capabilities

Confirm that the local AI/runtime can perform, directly or through an adapter:

- start/use a modern browser;
- navigate to a URL;
- inspect page content/accessibility semantics;
- click controls by semantic selector/text/role;
- type/select/upload when authorized;
- wait for UI/network state;
- capture screenshots;
- return current URL and visible error state;
- write structured JSON output to the repository workspace.

Recommended: console logs, network metadata, DOM/accessibility snapshots, traces/video.

## 4. OpenClaw integration discovery

Do not assume a specific OpenClaw CLI/API syntax from this repository. Inspect the actually installed OpenClaw version and its local help/documentation. Determine the safest stable invocation mechanism that can accept a job and return structured output.

Record the discovered adapter command/configuration locally. If repository code is needed to support this version, propose it as a change rather than embedding machine-specific assumptions into the protocol.

If OpenClaw cannot expose the required automation capabilities, report `BLOCKED` with the missing capability. Do not fake compatibility.

### Verified Windows bootstrap behavior

The canonical executor currently performs the OpenClaw bootstrap itself before preparing or starting the managed browser profile:

1. invoke `gateway start --json`;
2. reuse the existing Gateway when it is already running;
3. start the registered Gateway when it is stopped;
4. classify an inability to start the Gateway as an infrastructure error;
5. prepare/reuse the project-scoped browser profile;
6. start or reuse the browser and continue with the requested smoke scenario.

Manual Gateway startup is therefore **not a prerequisite** for normal QA runs.

This behavior was verified on the real Windows QA node with OpenClaw 2026.5.12 on 2026-09-23:

- GestionPisos Public Smoke run `35806072819`: **SUCCESS**.
- QA Node Handshake run `35806072832`: **SUCCESS**.
- Orchestrator commit: `3db8b882afc45d631204e133fc5a796c7ce920d3`.

The same public smoke also produced a real screenshot of the GestionPisos / Allaiso access screen through Chrome. These checks validate infrastructure/bootstrap only; they do **not** authorize authenticated sessions or AUT writes.

## 5. Runtime dependencies

The implementation milestone will provide a bootstrap script and lockfile. Until then, do not globally install speculative packages. Prefer project-local/virtual environments. Browser automation dependencies should be pinned once the adapter choice is verified.

## 6. Local configuration and secrets

Copy `config/runner.example.yaml` to a local ignored configuration file when the executor exists. Secret values belong in OS secret storage, environment variables, or GitHub Actions secrets—not YAML committed to Git.

Project profiles reference secret **names** only.

## 7. Verification handshake

Before testing a real AUT, prove these independently:

1. runner appears online in GitHub;
2. a harmless workflow is routed specifically to label `allaiso-qa`;
3. executor can parse a schema-valid demo job;
4. Gateway bootstrap succeeds without requiring manual startup;
5. browser opens a harmless test page;
6. screenshot is captured;
7. structured result is produced;
8. no secret appears in logs/evidence.

Only then register a real application under `projects/`.

## 8. What to tell the operator

Return a concise readiness report:

- OS/architecture;
- runner name/status;
- OpenClaw version and discovered invocation method;
- browser automation capabilities confirmed/missing;
- dependencies installed;
- security warnings;
- handshake PASS/BLOCKED and evidence location.
