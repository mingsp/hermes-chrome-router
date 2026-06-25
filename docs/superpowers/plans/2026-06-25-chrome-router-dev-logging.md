# Chrome Router Development Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `python run_router.py` show useful Chrome Router lifecycle logs by default while keeping secrets and raw browser payloads out of terminal output.

**Architecture:** Add one focused logging configuration module and instrument the existing Router runtime paths with standard-library `logging`. Keep protocol behavior unchanged: logging observes startup, binding refresh, cloud polling, WebSocket lifecycle, command dispatch, result posting, and cleanup without altering response payloads or control flow.

**Tech Stack:** Python 3.11, aiohttp, pytest, pytest-aiohttp, standard-library logging, pytest `caplog`.

---

## File Structure

- Create `router/logging_config.py`
  - Owns log-level parsing and one-time root logging setup.
  - Exposes `configure_logging()` and `parse_log_level()`.
- Modify `router/__main__.py`
  - Calls `configure_logging()` before loading config and starting aiohttp.
- Modify `router/app.py`
  - Adds `logger = logging.getLogger(__name__)`.
  - Logs a startup summary during aiohttp startup.
  - Logs binding refresh, poll loop, WebSocket lifecycle, command lifecycle, timeout, and cloud result failures.
  - Does not log tokens, raw params, or raw results.
- Create `tests/router/test_logging_config.py`
  - Covers default `INFO`, env override to `DEBUG`, and invalid level fallback.
- Modify `tests/router/test_router_app.py`
  - Adds focused `caplog` assertions for WebSocket hello and command failure logs.

## Task 1: Logging Configuration Module

**Files:**
- Create: `router/logging_config.py`
- Modify: `router/__main__.py`
- Test: `tests/router/test_logging_config.py`

- [ ] **Step 1: Write failing tests for log-level parsing**

Create `tests/router/test_logging_config.py`:

```python
import logging

from router.logging_config import parse_log_level


def test_parse_log_level_defaults_to_info():
    assert parse_log_level({}) == logging.INFO


def test_parse_log_level_uses_env_override():
    assert parse_log_level({"HERMES_CHROME_ROUTER_LOG_LEVEL": "debug"}) == logging.DEBUG


def test_parse_log_level_rejects_unknown_values():
    assert parse_log_level({"HERMES_CHROME_ROUTER_LOG_LEVEL": "verbose"}) == logging.INFO
```

- [ ] **Step 2: Run the new test and verify it fails**

Run:

```bash
python -m pytest tests/router/test_logging_config.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'router.logging_config'`.

- [ ] **Step 3: Implement the logging configuration module**

Create `router/logging_config.py`:

```python
from __future__ import annotations

import logging
import os
from collections.abc import Mapping


LOG_LEVEL_ENV = "HERMES_CHROME_ROUTER_LOG_LEVEL"
DEFAULT_LOG_LEVEL = logging.INFO


def parse_log_level(environ: Mapping[str, str] | None = None) -> int:
    values = environ if environ is not None else os.environ
    raw_level = str(values.get(LOG_LEVEL_ENV, "")).strip().upper()
    if not raw_level:
        return DEFAULT_LOG_LEVEL
    level = logging.getLevelName(raw_level)
    if isinstance(level, int):
        return level
    return DEFAULT_LOG_LEVEL


def configure_logging(environ: Mapping[str, str] | None = None) -> int:
    level = parse_log_level(environ)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("aiohttp.access").setLevel(level)
    return level
```

- [ ] **Step 4: Run the logging config tests**

Run:

```bash
python -m pytest tests/router/test_logging_config.py -q
```

Expected: PASS, 3 tests.

- [ ] **Step 5: Wire logging into the Router entrypoint**

Update `router/__main__.py` to:

```python
from aiohttp import web

from router.app import create_router_app
from router.config import load_config
from router.logging_config import configure_logging


def main() -> None:
    configure_logging()
    config = load_config()
    web.run_app(create_router_app(config), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run targeted tests**

Run:

```bash
python -m pytest tests/router/test_logging_config.py tests/router/test_config.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

```bash
git add router/logging_config.py router/__main__.py tests/router/test_logging_config.py
git commit -m "feat: configure chrome router logging"
```

## Task 2: Startup and Binding Refresh Logs

**Files:**
- Modify: `router/app.py`
- Test: `tests/router/test_router_app.py`

- [ ] **Step 1: Write failing startup binding refresh log test**

Append to `tests/router/test_router_app.py`:

```python
async def test_startup_logs_binding_refresh_summary(tmp_path, caplog):
    cloud = FakeCloudBridge()
    provider = FakeBindingProvider({"xuxiaofeng_profile": "span-macbook"})
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={},
    )
    app = create_router_app(config, cloud_bridge=cloud, binding_provider=provider, start_poller=False)
    client = TestClient(TestServer(app))
    with caplog.at_level("INFO", logger="router.app"):
        await client.start_server()
    try:
        messages = [record.getMessage() for record in caplog.records]
        assert any("binding_refresh_success" in message for message in messages)
        assert any("binding_count=1" in message for message in messages)
    finally:
        await client.close()
```

- [ ] **Step 2: Run the new test and verify it fails**

Run:

```bash
python -m pytest tests/router/test_router_app.py::test_startup_logs_binding_refresh_summary -q
```

Expected: FAIL because no `binding_refresh_success` log exists.

- [ ] **Step 3: Add logger and startup summary in `router/app.py`**

At the top of `router/app.py`, add the import and logger:

```python
import logging
```

Below the protocol imports, add:

```python
logger = logging.getLogger(__name__)
```

In `_on_startup`, replace the current function with:

```python
async def _on_startup(app: web.Application) -> None:
    config: RouterConfig = app["config"]
    logger.info(
        "router_start host=%s port=%s cloud_bridge_url=%s web_ui_bindings=%s binding_count=%s audit_enabled=%s min_client_version=%s",
        config.host,
        config.port,
        config.cloud_bridge_url,
        bool(app.get("binding_provider")),
        len(config.bindings),
        bool(app.get("audit_store")),
        config.min_client_version,
    )
    binding_provider = app.get("binding_provider")
    if binding_provider is not None:
        try:
            bindings, policies = await _load_binding_snapshot(app)
            app["registry"].update_bindings(bindings, policies)
            app["registry"].clear_cloud_result_error()
            logger.info(
                "binding_refresh_success source=startup binding_count=%s policy_count=%s",
                len(bindings),
                len(policies),
            )
        except Exception as exc:
            app["registry"].record_cloud_result_error(str(exc))
            logger.warning("binding_refresh_failed source=startup error=%s", exc)
    if app["start_poller"]:
        app["poll_task"] = asyncio.create_task(_poll_loop(app))
```

- [ ] **Step 4: Add refresh endpoint logs**

In `_refresh_bindings`, add logs while preserving response behavior:

```python
async def _refresh_bindings(request: web.Request) -> web.Response:
    config: RouterConfig = request.app["config"]
    if not _is_authorized_router_request(request, config):
        logger.info("binding_refresh_unauthorized source=endpoint")
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        bindings, policies = await _load_binding_snapshot(request.app)
    except Exception as exc:
        request.app["registry"].record_cloud_result_error(str(exc))
        logger.warning("binding_refresh_failed source=endpoint error=%s", exc)
        return web.json_response({"error": str(exc)}, status=502)
    request.app["registry"].update_bindings(bindings, policies)
    request.app["registry"].clear_cloud_result_error()
    logger.info(
        "binding_refresh_success source=endpoint binding_count=%s policy_count=%s",
        len(bindings),
        len(policies),
    )
    return web.json_response({"ok": True, "bindings": bindings, "actionPolicies": policies})
```

- [ ] **Step 5: Run targeted tests**

Run:

```bash
python -m pytest tests/router/test_router_app.py::test_startup_logs_binding_refresh_summary tests/router/test_router_app.py::test_refresh_bindings_endpoint_reloads_provider_snapshot -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add router/app.py tests/router/test_router_app.py
git commit -m "feat: log router startup and binding refresh"
```

## Task 3: WebSocket Lifecycle Logs

**Files:**
- Modify: `router/app.py`
- Modify: `tests/router/test_router_app.py`

- [ ] **Step 1: Write failing WebSocket hello log test**

Append to `tests/router/test_router_app.py`:

```python
async def test_bridge_ws_logs_authorized_hello(tmp_path, caplog):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={"xuxiaofeng_profile": "span-macbook"},
        min_client_version="0.2.0",
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        with caplog.at_level("INFO", logger="router.app"):
            ws = await client.ws_connect("/bridge")
            await ws.send_json(
                {
                    "type": "hello",
                    "deviceId": "span-macbook",
                    "token": "dev-token",
                    "clientVersion": "0.2.0",
                }
            )
            await ws.receive_json()
            await ws.close()
        messages = [record.getMessage() for record in caplog.records]
        assert any("bridge_hello_authorized" in message for message in messages)
        assert any("device_id=span-macbook" in message for message in messages)
        assert any("binding_count=1" in message for message in messages)
        assert any("bridge_disconnected" in message for message in messages)
    finally:
        await client.close()
```

- [ ] **Step 2: Run the WebSocket log test and verify it fails**

Run:

```bash
python -m pytest tests/router/test_router_app.py::test_bridge_ws_logs_authorized_hello -q
```

Expected: FAIL because `bridge_hello_authorized` is not logged.

- [ ] **Step 3: Add WebSocket lifecycle logs**

In `_bridge_ws`, add logs at these points:

After unauthorized hello:

```python
        if not await _authorize_hello(request.app, hello):
            logger.info("bridge_hello_unauthorized device_id=%s", hello.device_id)
            await ws.send_json({"type": "error", "error": "unauthorized"})
            await ws.close(code=4001, message=b"unauthorized")
            return ws
```

After version rejection:

```python
        if _is_client_version_too_old(hello.client_version, config.min_client_version):
            logger.info(
                "bridge_hello_version_rejected device_id=%s client_version=%s min_client_version=%s",
                hello.device_id,
                hello.client_version,
                config.min_client_version,
            )
            await ws.send_json(
                {
                    "type": "error",
                    "error": "version_too_old",
                    "minClientVersion": config.min_client_version,
                }
            )
            await ws.close(code=4003, message=b"version_too_old")
            return ws
```

After `registry.register(...)`:

```python
        logger.info(
            "bridge_hello_authorized device_id=%s client_version=%s binding_count=%s",
            hello.device_id,
            hello.client_version,
            len(registry.profile_ids_for_device(hello.device_id)),
        )
```

In the heartbeat branch before sending ack:

```python
                logger.debug(
                    "bridge_heartbeat device_id=%s extension_connected=%s extension_version=%s",
                    device_id,
                    bool(payload.get("extensionConnected")),
                    payload.get("extensionVersion") if isinstance(payload.get("extensionVersion"), str) else None,
                )
```

In the unsupported frame branch:

```python
            logger.debug("bridge_unsupported_frame device_id=%s frame_type=%s", device_id, payload.get("type"))
            await ws.send_json({"type": "error", "error": f"unsupported frame type {payload.get('type')}"})
```

In the `except (ProtocolError, DeliveryError, TimeoutError) as exc:` block:

```python
    except (ProtocolError, DeliveryError, TimeoutError) as exc:
        logger.info("bridge_error device_id=%s error=%s", device_id, exc)
        await ws.send_json({"type": "error", "error": str(exc)})
```

In `finally`, before failing in-flight commands:

```python
        if device_id:
            failed_commands = registry.unregister(device_id)
            logger.info(
                "bridge_disconnected device_id=%s failed_inflight_count=%s",
                device_id,
                len(failed_commands),
            )
            for command in failed_commands:
                await _fail_command(
                    request.app,
                    command,
                    f"browser bridge disconnected for profile {command.profile_id}",
                )
```

- [ ] **Step 4: Run targeted WebSocket tests**

Run:

```bash
python -m pytest tests/router/test_router_app.py::test_bridge_ws_logs_authorized_hello tests/router/test_router_app.py::test_management_overview_reports_router_connections_and_bindings -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add router/app.py tests/router/test_router_app.py
git commit -m "feat: log local client websocket lifecycle"
```

## Task 4: Command Lifecycle and Cloud Result Logs

**Files:**
- Modify: `router/app.py`
- Modify: `tests/router/test_router_app.py`

- [ ] **Step 1: Write failing command failure log test**

Append to `tests/router/test_router_app.py`:

```python
async def test_dispatch_logs_missing_binding_failure(tmp_path, caplog):
    cloud = FakeCloudBridge()
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={},
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)

    with caplog.at_level("INFO", logger="router.app"):
        await dispatch_command(
            app,
            CloudCommand(
                id="cmd_missing",
                profile_id="missing_profile",
                action="page.click",
                params={"selector": "#submit", "secret": "do-not-log"},
            ),
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any("command_received" in message for message in messages)
    assert any("command_delivery_failed" in message for message in messages)
    assert any("command_id=cmd_missing" in message for message in messages)
    assert "do-not-log" not in "\n".join(messages)
```

- [ ] **Step 2: Write failing cloud result failure log test**

Append to `tests/router/test_router_app.py`:

```python
async def test_dispatch_logs_cloud_result_post_failure(tmp_path, caplog):
    cloud = FakeCloudBridge()
    cloud.fail_results = True
    config = RouterConfig(
        host="127.0.0.1",
        port=0,
        cloud_bridge_url="http://cloud",
        router_token="dev-token",
        bindings_path=tmp_path / "bindings.json",
        bindings={},
    )
    app = create_router_app(config, cloud_bridge=cloud, start_poller=False)

    with caplog.at_level("WARNING", logger="router.app"):
        await dispatch_command(
            app,
            CloudCommand(
                id="cmd_cloud_down",
                profile_id="missing_profile",
                action="page.snapshot",
                params={"secret": "do-not-log"},
            ),
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any("cloud_result_post_failed" in message for message in messages)
    assert any("command_id=cmd_cloud_down" in message for message in messages)
    assert "do-not-log" not in "\n".join(messages)
```

- [ ] **Step 3: Run command log tests and verify they fail**

Run:

```bash
python -m pytest tests/router/test_router_app.py::test_dispatch_logs_missing_binding_failure tests/router/test_router_app.py::test_dispatch_logs_cloud_result_post_failure -q
```

Expected: FAIL because command lifecycle logs are not present.

- [ ] **Step 4: Add command lifecycle logs**

In `dispatch_command`, add:

```python
async def dispatch_command(app: web.Application, command) -> None:
    logger.info(
        "command_received command_id=%s profile_id=%s action=%s",
        command.id,
        command.profile_id,
        command.action,
    )
    if _is_rate_limited(app, command.profile_id):
        logger.info(
            "command_rate_limited command_id=%s profile_id=%s action=%s",
            command.id,
            command.profile_id,
            command.action,
        )
        await _post_cloud_result(
            app,
            command.id,
            False,
            profile_id=command.profile_id,
            error=f"rate limit exceeded for profile {command.profile_id}",
        )
        return
    transit = TransitCommand(
        id=command.id,
        profile_id=command.profile_id,
        cloud_bridge_url=app["config"].cloud_bridge_url,
        action=command.action,
        params=command.params,
        timeout_ms=ROUTER_DELIVERY_TIMEOUT_MS,
    )
    await _enqueue_local_command(app, transit)
```

In `_enqueue_local_command`, before posting failure:

```python
        logger.info(
            "command_delivery_failed command_id=%s profile_id=%s action=%s error=%s",
            command.id,
            command.profile_id,
            command.action,
            exc,
        )
```

After appending to the queue:

```python
    logger.info(
        "command_queued command_id=%s profile_id=%s action=%s device_id=%s queue_depth=%s",
        command.id,
        command.profile_id,
        command.action,
        connection.device_id,
        len(app["queues"][command.profile_id]),
    )
```

In `_dispatch_next_for_profile`, before `send_json`:

```python
            logger.info(
                "command_delivered command_id=%s profile_id=%s action=%s device_id=%s",
                command.id,
                command.profile_id,
                command.action,
                connection.device_id,
            )
```

In the `send_json` exception branch:

```python
            logger.info(
                "command_delivery_failed command_id=%s profile_id=%s action=%s device_id=%s error=%s",
                command.id,
                command.profile_id,
                command.action,
                connection.device_id,
                exc,
            )
```

In the outer `DeliveryError` branch:

```python
        logger.info(
            "command_delivery_failed command_id=%s profile_id=%s action=%s error=%s",
            command.id,
            command.profile_id,
            command.action,
            exc,
        )
```

- [ ] **Step 5: Add result, timeout, and cloud result logs**

In `_bridge_ws`, after parsing `result`:

```python
                logger.info(
                    "command_result_received command_id=%s profile_id=%s ok=%s",
                    result.id,
                    result.profile_id,
                    result.ok,
                )
```

In `_timeout_command`, before `_fail_command`:

```python
    logger.info(
        "command_timeout command_id=%s profile_id=%s action=%s timeout_ms=%s",
        command.id,
        command.profile_id,
        command.action,
        command.timeout_ms,
    )
```

In `_fail_command`, before `_post_cloud_result`:

```python
    logger.info(
        "command_failed command_id=%s profile_id=%s action=%s error=%s",
        command.id,
        command.profile_id,
        command.action,
        error,
    )
```

In `_post_cloud_result`, after successful post:

```python
        logger.info(
            "cloud_result_posted command_id=%s profile_id=%s ok=%s",
            command_id,
            profile_id,
            ok,
        )
```

In `_post_cloud_result`, inside `except Exception as exc:`:

```python
        logger.warning(
            "cloud_result_post_failed command_id=%s profile_id=%s ok=%s error=%s",
            command_id,
            profile_id,
            ok,
            exc,
        )
```

- [ ] **Step 6: Run targeted command log tests**

Run:

```bash
python -m pytest tests/router/test_router_app.py::test_dispatch_logs_missing_binding_failure tests/router/test_router_app.py::test_dispatch_logs_cloud_result_post_failure -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add router/app.py tests/router/test_router_app.py
git commit -m "feat: log chrome command lifecycle"
```

## Task 5: Poll Loop Logs and Full Verification

**Files:**
- Modify: `router/app.py`
- Test: existing Router test suite

- [ ] **Step 1: Add poll loop logs**

In `_poll_loop`, replace the current function with:

```python
async def _poll_loop(app: web.Application) -> None:
    router_id = uuid.uuid4().hex[:8]
    cloud_bridge = app["cloud_bridge"]
    empty_polls = 0
    logger.info("poll_loop_start router_id=%s", router_id)
    while True:
        try:
            command = await cloud_bridge.poll_next(router_id)
            if isinstance(command, MalformedCloudCommand):
                logger.info("cloud_command_malformed command_id=%s error=%s", command.id, command.error)
                await _post_cloud_result(app, command.id, False, error=command.error)
            elif command is not None:
                empty_polls = 0
                await dispatch_command(app, command)
            else:
                empty_polls += 1
                if empty_polls % 1200 == 0:
                    logger.debug("poll_loop_idle router_id=%s empty_polls=%s", router_id, empty_polls)
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            logger.info("poll_loop_cancelled router_id=%s", router_id)
            raise
        except Exception as exc:
            app["registry"].record_cloud_result_error(str(exc))
            logger.warning("poll_loop_error router_id=%s error=%s", router_id, exc, exc_info=logger.isEnabledFor(logging.DEBUG))
            await asyncio.sleep(0.2)
```

- [ ] **Step 2: Run focused app tests**

Run:

```bash
python -m pytest tests/router/test_router_app.py -q
```

Expected: PASS.

- [ ] **Step 3: Run full automated suite**

Run:

```bash
python -m pytest -q
```

Expected: PASS.

- [ ] **Step 4: Manually verify startup output without touching hermes-web-ui**

Run from `/Users/span/workspace/hermes-space/hermes_chrome_router` with a throwaway local env file that points at an unused local cloud bridge URL:

```bash
HERMES_CHROME_ROUTER_ENV_FILE=.env python run_router.py
```

Expected terminal output includes a line similar to:

```text
INFO router.app router_start host=127.0.0.1 port=8787 cloud_bridge_url=...
```

Stop only this Router process with `Ctrl-C`. Do not start, stop, or restart `hermes-web-ui`.

- [ ] **Step 5: Commit Task 5**

```bash
git add router/app.py
git commit -m "feat: log chrome router poll loop"
```

## Task 6: README Developer Logging Notes

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add README section**

In `README.md`, after the "Run the Router" command block, add:

~~~markdown
Development logging defaults to `INFO`, so `python run_router.py` prints startup, binding, Local Client connection, cloud command, delivery, and result-posting events. For more frame-level detail, set:

```bash
export HERMES_CHROME_ROUTER_LOG_LEVEL=DEBUG
python run_router.py
```

Logs intentionally omit router tokens, bridge tokens, raw command params, raw command results, page content, and screenshots.
~~~

- [ ] **Step 2: Check README formatting**

Run:

```bash
python -m pytest tests/router/test_logging_config.py -q
```

Expected: PASS. This confirms the logging env var documented in README still matches the code.

- [ ] **Step 3: Commit Task 6**

```bash
git add README.md
git commit -m "docs: document chrome router development logging"
```

## Final Verification

- [ ] **Step 1: Check worktree diff**

Run:

```bash
git status --short
```

Expected: only pre-existing unrelated changes remain, or a clean worktree if the implementation branch contained only this work.

- [ ] **Step 2: Run full test suite**

Run:

```bash
python -m pytest -q
```

Expected: PASS.

- [ ] **Step 3: Inspect logs for secret hygiene**

Run:

```bash
rg -n "token|Authorization|params|result" router tests/router/test_router_app.py README.md
```

Expected: code may reference these concepts, but log message strings must not include raw token values, authorization headers, raw command params, or raw result payloads.

- [ ] **Step 4: Summarize implementation**

Report:

- files changed
- tests run
- whether `python run_router.py` startup output was manually checked
- confirmation that `hermes-web-ui` was not started, stopped, or restarted
