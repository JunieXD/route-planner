# AMap Web Service key setup

Read this file only when the AMap adapter reports a missing credential, or when helping someone install, update, inspect, or remove its key.

## Obtain the correct key

Create an application in the [AMap developer console](https://console.amap.com/dev/key/app), add a key, and select **Web Service** as the platform. A browser JavaScript key is not interchangeable with a Web Service key.

Never ask the user to paste the key into chat. The bundled manager prompts with hidden input and never prints the secret:

```bash
python3 scripts/amap_credentials.py set
python3 scripts/amap_credentials.py status
```

`set` also updates an existing credential. The route adapter resolves credentials in this order:

1. `AMAP_MAPS_API_KEY` environment variable;
2. native operating-system credential store;
3. a missing-key error with a setup hint.

The environment variable deliberately takes precedence so CI, containers, and one-off overrides remain possible.

## macOS Keychain

Run from the skill directory:

```bash
python3 scripts/amap_credentials.py set
```

The command stores a generic password in the user's login Keychain with:

- service: `codex.route-planner.amap-api-key`
- account: the current macOS account name

Input is hidden and the key is not placed in shell history or process arguments. macOS may ask the user to unlock the login Keychain or approve access. The reader remains compatible with the earlier service name `codex.amap.maps-api-key`.

Inspect presence without revealing the value:

```bash
python3 scripts/amap_credentials.py status
```

Delete only when the user explicitly asks:

```bash
python3 scripts/amap_credentials.py delete
```

## Windows Credential Manager

The same manager uses the native Windows Credential Manager through the standard Python library only:

```powershell
py scripts\amap_credentials.py set
py scripts\amap_credentials.py status
```

It stores a Generic Credential with target:

```text
Codex/route-planner/AMAP_MAPS_API_KEY
```

读取时仍兼容旧版目标 `Codex/china-multimodal-route-planner/AMAP_MAPS_API_KEY`；新写入的凭据统一使用新名称。

Delete only when explicitly requested:

```powershell
py scripts\amap_credentials.py delete
```

No extra PowerShell module or third-party Python package is required.

## Linux, CI, and containers

There is no default Linux secret-store dependency. Inject `AMAP_MAPS_API_KEY` through the process environment or the platform's secret manager. Do not write the value into the skill, source control, fixtures, logs, or command examples.
