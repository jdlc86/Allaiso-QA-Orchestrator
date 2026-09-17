# Projects / Applications Under Test

This directory registers applications without coupling the QA node to them.

Recommended layout:

```text
projects/<project_id>/project.yaml
projects/<project_id>/README.md
```

A project profile should contain only non-secret configuration:

```yaml
project_id: example-app
display_name: Example App
allowed_environments:
  test:
    base_url: https://example.invalid
required_capabilities:
  - browser
  - screenshot
secret_names:
  - EXAMPLE_TEST_USERNAME
  - EXAMPLE_TEST_PASSWORD
safety:
  production_allowed: false
  destructive_tests_allowed: false
```

Never commit secret values, session cookies, private tokens, or real user credentials. A project may be tested by many orchestrator sessions; the stable `project_id` is what binds jobs/results to the correct AUT.
