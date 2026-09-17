# Architecture

## Goal

Provide a reusable, auditable bridge that lets independent AI sessions delegate browser/computer QA to a workstation without coupling the workstation to a single application or chat.

## Components

### 1. Orchestrator AI
Understands product intent and creates coarse-grained test jobs. It should not stream individual clicks through GitHub unless diagnosing a specific failure.

### 2. GitHub control plane
Durable exchange for protocol, project manifests, jobs, results and selected evidence. It also provides audit history and can dispatch work to a self-hosted runner.

### 3. Self-hosted GitHub Actions runner
Outbound-connected worker installed on the QA workstation. GitHub assigns approved jobs to it. It must use dedicated labels such as `self-hosted`, OS label, and `allaiso-qa` so unrelated workflows cannot accidentally execute on this machine.

### 4. Local executor adapter
A small vendor-neutral program that validates a job, obtains a lock, invokes the available computer/browser automation runtime (initially OpenCloud), normalizes evidence, validates the result schema, and publishes output.

OpenCloud is therefore an **adapter target**, not the protocol itself. A future Playwright-only agent, another desktop AI, or another workstation can implement the same contract.

### 5. Browser/computer automation
Required baseline capabilities:
- launch/navigate browser;
- inspect visible/accessible UI;
- semantic click/type/select;
- wait/retry/reload;
- screenshots;
- read URL/title/text;
- assertions.

Strongly recommended diagnostics:
- browser console capture;
- network request/response metadata (with secrets redacted);
- DOM/accessibility snapshot;
- Playwright trace/video when supported.

### 6. Project profiles
Each AUT gets a stable `project_id` and profile describing target URLs, permitted environments, capabilities, secret *names* (never values), and safety constraints.

## Isolation model

```text
projects/app-a/...     jobs/app-a/...     results/app-a/...     evidence/app-a/...
projects/app-b/...     jobs/app-b/...     results/app-b/...     evidence/app-b/...
```

Every run additionally owns a unique `run_id`. No project may read another project's secrets or reuse authenticated browser state unless explicitly configured.

## Job state machine

`QUEUED -> CLAIMED -> RUNNING -> PASSED | FAILED | BLOCKED | ERROR | CANCELLED`

Claims must eventually include a lease/heartbeat so a crashed executor does not lock a job forever. The first implementation may serialize all runs on one workstation; concurrency comes only after locking is proven.

## Communication direction

The workstation should normally make outbound connections to GitHub. Do not expose a general-purpose inbound remote-control port merely to receive tests.

## Why jobs are bundles

GitHub is an asynchronous control plane, not a low-latency remote desktop protocol. A job should carry enough reasoning boundaries for the executor to complete a useful scenario independently. Diagnostic branches can say, for example: if Save does not become enabled, inspect validation text and capture evidence before stopping.

## Multi-agent operation

Different ChatGPT/AI sessions can submit independent jobs. `requested_by` identifies the logical orchestrator. Correctness comes from immutable IDs and repository state, not from assuming a particular session remains alive.

## Trust boundary

A self-hosted runner can execute code on the workstation. Therefore workflows and job inputs are privileged. Protect the repository, restrict who can modify executable workflows/scripts, avoid untrusted pull-request execution on the runner, and keep the runner dedicated to QA rather than a personal/admin workstation when possible.
