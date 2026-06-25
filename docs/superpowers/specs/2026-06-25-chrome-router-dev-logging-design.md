# Chrome Router Development Logging Design

## Context

`python run_router.py` currently loads `.env` and calls `router.__main__.main()`. The Router starts through `aiohttp.web.run_app()`, but the application code has almost no runtime logging. During development this makes the service look silent even when it is polling the cloud bridge, accepting Local Client WebSocket connections, loading bindings, dispatching commands, or failing a command back to the cloud bridge.

The Router already has `/status`, `/metrics`, and optional command audit storage, but those are query surfaces after the fact. This design adds foreground runtime logs for local development and operator diagnosis.

## Goals

- Make `python run_router.py` show useful lifecycle logs by default.
- Use `INFO` as the default log level.
- Allow more detail with `HERMES_CHROME_ROUTER_LOG_LEVEL=DEBUG`.
- Avoid logging secrets, raw command params, raw command results, page text, screenshots, or bearer tokens.
- Keep the change local to `hermes_chrome_router`; do not start, stop, or restart `hermes-web-ui`.
- Avoid new dependencies.

## Non-Goals

- Do not replace `/status`, `/metrics`, or command audit storage.
- Do not introduce JSON logging or a production log aggregation contract yet.
- Do not change Router protocol behavior, Local Client frames, cloud bridge endpoints, or authorization rules.
- Do not add file logging or log rotation in this phase.

## Approach

Use Python standard-library `logging`.

`router.__main__` will configure logging once before the app starts. It will read:

- `HERMES_CHROME_ROUTER_LOG_LEVEL`: optional level name, default `INFO`.

The format should be compact and readable in a terminal:

```text
2026-06-25 10:15:30 INFO router.app router_start host=127.0.0.1 port=8787 cloud_bridge_url=http://...
```

Modules with runtime behavior will use `logger = logging.getLogger(__name__)`.

## INFO Events

Log these events at `INFO`:

- Router startup summary:
  - host
  - port
  - cloud bridge URL
  - whether Web UI binding provider is enabled
  - configured binding count
  - audit DB enabled or disabled
  - minimum client version
- Startup binding refresh success or failure.
- Poll loop start.
- Cloud command received:
  - command id
  - profile id
  - action
- Malformed cloud command result posted.
- Command rate limited.
- Command queued.
- Command delivered to Local Client.
- Command result received from Local Client:
  - command id
  - profile id
  - ok status
- Command timeout.
- Binding missing, Local Client offline, or delivery failure.
- Cloud result post failure.
- Local Client WebSocket authorized hello:
  - device id
  - client version
  - bound profile count
- Local Client unauthorized hello.
- Local Client version rejected.
- Local Client disconnect, including failed in-flight command count.
- `/bindings/refresh` success or failure.

## DEBUG Events

Log these events at `DEBUG`:

- Heartbeat received and acknowledged.
- Unsupported WebSocket frame type.
- Poll loop empty results only as suppressed periodic state, not every 50 ms.
- Full exception stack traces for unexpected exceptions using `exc_info=True`.

## Sensitive Data Rules

Logs must not include:

- `HERMES_CHROME_ROUTER_TOKEN`
- Local Client bridge tokens
- Authorization headers
- raw command params
- raw command results
- page content
- screenshots

Allowed identifiers:

- command id
- profile id
- device id
- action name
- sanitized error message
- counts and booleans

## Error Handling

Logging must be added beside existing behavior without changing control flow.

Existing paths that record `lastCloudResultError`, post failed results, close WebSockets, or dispatch the next queued command should continue to do the same thing. Logs should make those transitions visible but must not become required for correctness.

## Tests

Add focused tests using `caplog`:

- Default log level is `INFO` when the env var is absent.
- `HERMES_CHROME_ROUTER_LOG_LEVEL=DEBUG` enables debug logging.
- Startup logs summarize binding provider mode and binding count.
- WebSocket hello success and rejection paths emit useful logs.
- Command failure due to missing binding or offline client emits an `INFO` log.
- Cloud result post failure emits a warning or error log without exposing raw params.

Existing Router tests should continue to pass with no changes to protocol payloads.

## Acceptance Criteria

- Running `python run_router.py` prints a startup summary without extra flags.
- During normal development, a user can see whether the Router is polling, whether a Local Client connected, and how each command moved through queued, delivered, completed, failed, or timed out states.
- `DEBUG` provides additional frame and heartbeat visibility without flooding empty poll logs.
- No secrets or raw browser/page payloads appear in logs.
- Automated tests cover the logging configuration and representative lifecycle logs.
