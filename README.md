# Hermes Chrome Router

This directory contains the source code for:

- Command Router / Remote Extension Proxy
- Router protocol harness for fake cloud bridge and fake extension traffic

You can run it directly from source; installing it as a Python package is optional. The formal Hermes Local Client lives in `/Users/span/workspace/hermes-space/hermes-local-client` and uses Wails v3 with a Go backend and React renderer. `hermes-web-ui` has the server-side profile binding and device bridge token foundations used by the Router. The Router is intentionally single-instance for the internal small-team deployment path.

## Run From Source

Directory layout:

```text
router/       Command Router service
shared/       shared protocol and timing helpers
harness/      headless test harness that simulates the user-side Local Client protocol
tests/        unit and integration tests
run_router.py direct Router entrypoint
```

Create a virtual environment and install runtime dependencies:

```bash
cd /Users/span/workspace/hermes-space/hermes_chrome_router
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a local Router config file:

```bash
cp .env.example .env
```

Edit `.env` for your deployment. `run_router.py` loads `.env` automatically. To use a different file, set `HERMES_CHROME_ROUTER_ENV_FILE=/path/to/router.env`.

Run the Router:

```bash
python run_router.py
```

Development logging defaults to `INFO`, so `python run_router.py` prints startup, binding, Local Client connection, cloud command, delivery, and result-posting events. For more frame-level detail, set:

```bash
export HERMES_CHROME_ROUTER_LOG_LEVEL=DEBUG
python run_router.py
```

Logs intentionally omit router tokens, bridge tokens, raw command params, raw command results, page content, and screenshots.

The equivalent module form is `python -m router`, but `python run_router.py` is the intended source-run command.

## Run With Docker Compose

The production container is built from the current checkout; it does not pull a prebuilt Router image. Keep secrets in `.env`, which is excluded from the Docker build context.

On Linux, the Router uses host networking because its cloud bridge and Web UI may listen only on the host loopback interface. With host networking, `127.0.0.1:16319` and `127.0.0.1:8648` resolve to the host services rather than to an isolated container network.

```bash
cp .env.example .env
chmod 600 .env
mkdir -p ./data
docker compose build
docker compose up -d
docker compose ps
curl http://127.0.0.1:8787/health
```

`compose.yaml` runs the Router as a non-root UID/GID, uses a read-only root filesystem, and persists the audit database through `HERMES_CHROME_ROUTER_DATA_DIR` (default `./data`). Set that variable to an absolute server data directory when deploying outside a development checkout.

Do not use this host-network Compose configuration on Docker Desktop for macOS as a production-equivalent network test. It is intended for the Linux Router host.

For development tests, install test dependencies:

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Optional editable package install is still supported when you want CLI commands such as `hermes-chrome-router`:

```bash
pip install -e '.[dev]'
hermes-chrome-router
```

## Router Environment

- `HERMES_CHROME_ROUTER_HOST`: bind host, default `127.0.0.1`
- `HERMES_CHROME_ROUTER_PORT`: bind port, default `8787`
- `HERMES_CHROME_CLOUD_BRIDGE_URL`: fake or internal cloud bridge base URL
- `HERMES_CHROME_ROUTER_TOKEN`: development/internal fallback token for Local Client hello frames and Router-to-cloud-bridge `Authorization: Bearer ...`
- `HERMES_CHROME_WEB_UI_URL`: remote hermes-web-ui base URL used to load `/api/hermes/browser/profile-bindings` and verify `/api/devices/bridge-token/verify`
- `HERMES_CHROME_BINDINGS_PATH`: optional development JSON file mapping `profileId` to `deviceId` when `HERMES_CHROME_WEB_UI_URL` is not used
- `HERMES_CHROME_ROUTER_AUDIT_DB`: optional SQLite database path for the cloud-side command audit index. When set, Router records command lifecycle events with profile/action/status/detail and a SHA-256 params hash, not raw command params.
- `HERMES_CHROME_ROUTER_PUBLIC_URL`: public WebSocket URL returned by `hermes-web-ui` device authorization callbacks, for example `wss://router.example.com/bridge`

## Router Endpoints

- `GET /health`: liveness probe
- `GET /status`: protected JSON router state for web-ui device status merging when `HERMES_CHROME_ROUTER_TOKEN` is configured, including per-profile command totals and error rates
- `GET /status/profile/{profileId}`: protected profile diagnostics when `HERMES_CHROME_ROUTER_TOKEN` is configured, including that profile's command totals and error rate
- `GET /metrics`: protected Prometheus text metrics when `HERMES_CHROME_ROUTER_TOKEN` is configured, including connected Local Clients, bound profiles, in-flight commands, queued commands, cloud result channel health, per-profile online state, and per-profile command totals/errors/error rate
- `GET /audit/commands`: protected searchable command audit index when `HERMES_CHROME_ROUTER_AUDIT_DB` is configured. Supports `profileId`, `commandId`, `deviceId`, `action`, `status`, `q`, `fromMs`, `toMs`, `limit`, and `offset` query parameters. Responses include `count`, `total`, `limit`, `offset`, and `items`.
- `GET /audit/summary`: protected command audit operations summary when `HERMES_CHROME_ROUTER_AUDIT_DB` is configured. Supports the same filters as `/audit/commands` and returns total/failed/profile/device counts plus grouped profile/status/action totals for cloud management views.
- `POST /bindings/refresh`: protected command for web-ui to refresh Router profile bindings after profile binding save/unbind operations
- `GET /bridge`: Hermes Local Client WebSocket entrypoint

## Headless Protocol Harness Environment

- `HERMES_CHROME_ROUTER_URL`: Router WebSocket URL, for example `ws://127.0.0.1:8787/bridge`
- `HERMES_CHROME_ROUTER_TOKEN`: development fallback token; production Local Clients should use a web-ui issued device bridge token after the device is approved
- `HERMES_CHROME_DEVICE_ID`: local development device id
- `HERMES_CHROME_PROFILE_IDS`: comma-separated profile ids announced to Router
- `HERMES_CHROME_LOCAL_HOST`: local bridge host, default `127.0.0.1`
- `HERMES_CHROME_LOCAL_PORT`: local bridge port, default `16319`
- `HERMES_CHROME_LOCAL_CONFIG_DIR`: optional harness config directory. When the router URL/token/device env vars are absent, the harness reads `config.json` plus a separate `token` file from this directory.

## Headless Protocol Harness Authorization Callback

The headless Python protocol harness includes a localhost callback app for development and Router tests only. It is not the formal desktop client backend. The formal Wails Hermes Local Client uses the custom protocol callback by default.

- `GET /auth/callback?serverUrl=...&routerUrl=...&deviceId=...&token=...&profileIds=a,b`
- non-secret settings are written to `config.json`
- the bridge token is written to a separate `token` file with `0600` permissions

`hermes-web-ui` can generate this callback URL when issuing a device bridge token:

- `HERMES_CHROME_LOCAL_CLIENT_CALLBACK_URL`: optional callback base URL, default `hermes-local-client://auth/callback` in `hermes-web-ui`; use `http://127.0.0.1:16320/auth/callback` only for the headless Python harness
- `HERMES_CHROME_ROUTER_PUBLIC_URL`: optional public Router WebSocket URL. If unset, web-ui derives `ws://.../bridge` or `wss://.../bridge` from the current request host/protocol.

This is a development-safe credential store boundary for the harness. The Wails / Go Hermes Local Client stores the production device bridge token in macOS Keychain or Windows Credential Manager.

Do not start, stop, or restart `hermes-web-ui` for this MVP.

## Internal Acceptance Bundle

The final remote Chrome evidence can also be collected with the two-phase runner. First generate readiness and the audit start timestamp:

```bash
python -m router.remote_acceptance_run --phase readiness --archive-dir ./artifacts/hermes-chrome-acceptance
```

`--output-dir` is accepted as an alias for `--archive-dir` when this runner is wired into generic artifact jobs.

Then run the real Mac Chrome actions from the remote `hermes-web-ui` against the target profile. After `tab.list`, `page.snapshot`, and `page.click` have completed, collect Router audit evidence, the Router bundle, and the archive report:

```bash
python -m router.remote_acceptance_run --phase collect --archive-dir ./artifacts/hermes-chrome-acceptance
```

The runner only writes and validates evidence files. It does not replace the manual Mac Chrome + Hermes Chrome Extension E2E step.

After the real remote Chrome E2E run has been executed, capture a single evidence bundle:

```bash
python -m router.acceptance_bundle --output ./artifacts/chrome-router-acceptance.json
```

or install the package and run:

```bash
hermes-chrome-router-acceptance-bundle --output ./artifacts/chrome-router-acceptance.json
```

The bundle runs `e2e_acceptance`, prints the combined JSON report, and optionally writes it to disk.

For the full production execution order and required evidence files, see `/Users/span/workspace/hermes-space/docs/hermes-chrome-production-acceptance-runbook.md`.

After collecting the full artifact directory, validate the archive shape:

```bash
python -m router.acceptance_archive --archive-dir ./artifacts/hermes-chrome-acceptance
```

or install the package and run:

```bash
hermes-chrome-router-acceptance-archive --archive-dir ./artifacts/hermes-chrome-acceptance
```

## Remote Chrome E2E Readiness

Before a real remote web-ui + Hermes Local Client + Chrome Extension manual E2E run, configure:

```bash
export HERMES_CHROME_WEB_UI_URL=https://hermes.example.com
export HERMES_CHROME_ROUTER_STATUS_URL=https://router.example.com
export HERMES_CHROME_ROUTER_TOKEN=...
export HERMES_CHROME_E2E_PROFILE_ID=xuxiaofeng_profile
export HERMES_CHROME_LOCAL_BRIDGE_URL=http://127.0.0.1:16319
```

Then run:

```bash
python -m router.e2e_readiness
```

or install the package and run:

```bash
hermes-chrome-router-e2e-doctor
```

The command emits a JSON readiness report for:

- remote web-ui Local Client update manifest reachability;
- Router `GET /status/profile/{profileId}` with bearer token;
- Router `chrome_*` tool gate readiness for the target profile;
- local Browser Bridge Service `/status` reachability;
- Hermes Chrome Extension poll status on the local bridge.

This is a preflight check only. Real acceptance still requires opening Mac Chrome, loading the real Hermes Chrome Extension, and running actual `chrome_*` actions against the target remote profile.

After the operator runs real `chrome_*` actions from the remote web-ui against Mac Chrome, verify Router audit evidence:

```bash
export HERMES_CHROME_E2E_FROM_MS=1718500000000
python -m router.e2e_acceptance
```

or install the package and run:

```bash
hermes-chrome-router-e2e-acceptance
```

The acceptance report reuses the readiness checks, then queries protected Router `/audit/commands` for completed `tab.list`, `page.snapshot`, and `page.click` events on `HERMES_CHROME_E2E_PROFILE_ID`. Override the required action list with `HERMES_CHROME_E2E_REQUIRED_ACTIONS=tab.list,page.snapshot,page.click`.

## Development Verification

Run:

```bash
python -m pytest -q
```

The automated suite uses fake cloud bridge and fake extension traffic. It does not start, stop, or restart `hermes-web-ui`.
