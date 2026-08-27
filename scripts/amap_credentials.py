#!/usr/bin/env python3
"""Manage the AMap key in the environment or native OS credential store."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
from typing import Any

KEY_ENV = "AMAP_MAPS_API_KEY"
MACOS_SERVICE = "route-planner.amap-api-key"
MACOS_LEGACY_SERVICES = (
    "codex.route-planner.amap-api-key",
    "codex.amap.maps-api-key",
)
WINDOWS_TARGET = "RoutePlanner/AMAP_MAPS_API_KEY"
WINDOWS_LEGACY_TARGETS = (
    "Codex/route-planner/AMAP_MAPS_API_KEY",
    "Codex/china-multimodal-route-planner/AMAP_MAPS_API_KEY",
)


class CredentialError(RuntimeError):
    pass


def account_name() -> str:
    return getpass.getuser()


def backend_name() -> str:
    if sys.platform == "darwin":
        return "macos-keychain"
    if os.name == "nt":
        return "windows-credential-manager"
    return "environment-only"


def _macos_security(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["/usr/bin/security", *args],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise CredentialError(f"macOS Keychain command failed: {error}") from error


def _macos_native_api() -> tuple[Any, Any, Any]:
    import ctypes

    security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
    core = ctypes.CDLL(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )
    security.SecKeychainFindGenericPassword.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
    security.SecKeychainAddGenericPassword.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    security.SecKeychainAddGenericPassword.restype = ctypes.c_int32
    security.SecKeychainItemModifyAttributesAndData.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    security.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
    security.SecKeychainItemDelete.argtypes = [ctypes.c_void_p]
    security.SecKeychainItemDelete.restype = ctypes.c_int32
    security.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    security.SecKeychainItemFreeContent.restype = ctypes.c_int32
    core.CFRelease.argtypes = [ctypes.c_void_p]
    core.CFRelease.restype = None
    return ctypes, security, core


def _macos_item(service_name: str) -> tuple[Any, Any, Any, Any] | None:
    ctypes, security, core = _macos_native_api()
    service = service_name.encode("utf-8")
    account = account_name().encode("utf-8")
    item = ctypes.c_void_p()
    status = security.SecKeychainFindGenericPassword(
        None,
        len(service),
        service,
        len(account),
        account,
        None,
        None,
        ctypes.byref(item),
    )
    if status == -25300:
        return None
    if status != 0:
        raise CredentialError(f"macOS Keychain lookup failed (status {status})")
    return ctypes, security, core, item


def _read_macos_native(service_name: str) -> str | None:
    ctypes, security, core = _macos_native_api()
    service = service_name.encode("utf-8")
    account = account_name().encode("utf-8")
    length = ctypes.c_uint32()
    data = ctypes.c_void_p()
    item = ctypes.c_void_p()
    status = security.SecKeychainFindGenericPassword(
        None,
        len(service),
        service,
        len(account),
        account,
        ctypes.byref(length),
        ctypes.byref(data),
        ctypes.byref(item),
    )
    if status == -25300:
        return None
    if status != 0:
        raise CredentialError(f"macOS Keychain read failed (status {status})")
    try:
        return ctypes.string_at(data, length.value).decode("utf-8") or None
    finally:
        if data.value:
            security.SecKeychainItemFreeContent(None, data)
        if item.value:
            core.CFRelease(item)


def _read_macos_legacy(service_name: str) -> str | None:
    result = _macos_security(
        ["find-generic-password", "-a", account_name(), "-s", service_name, "-w"]
    )
    if result.returncode == 0:
        return result.stdout.rstrip("\r\n") or None
    message = result.stderr.lower()
    if result.returncode == 44 or "could not be found" in message:
        return None
    raise CredentialError("macOS Keychain could not read the legacy AMap key")


def read_macos_keychain() -> str | None:
    value = _read_macos_native(MACOS_SERVICE)
    if value:
        return value
    for service_name in MACOS_LEGACY_SERVICES:
        value = _read_macos_legacy(service_name)
        if value:
            return value
    return None


def write_macos_keychain(key: str) -> None:
    if any(character in key for character in ("\x00", "\r", "\n")):
        raise CredentialError("AMap key contains an unsupported control character")
    ctypes, security, core = _macos_native_api()
    raw = key.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    existing = _macos_item(MACOS_SERVICE)
    if existing:
        _, _, _, item = existing
        try:
            status = security.SecKeychainItemModifyAttributesAndData(
                item,
                None,
                len(raw),
                ctypes.cast(buffer, ctypes.c_void_p),
            )
        finally:
            core.CFRelease(item)
    else:
        service = MACOS_SERVICE.encode("utf-8")
        account = account_name().encode("utf-8")
        status = security.SecKeychainAddGenericPassword(
            None,
            len(service),
            service,
            len(account),
            account,
            len(raw),
            ctypes.cast(buffer, ctypes.c_void_p),
            None,
        )
    if status != 0:
        raise CredentialError(
            f"macOS Keychain could not store the AMap key (status {status})"
        )


def delete_macos_keychain() -> bool:
    deleted = False
    existing = _macos_item(MACOS_SERVICE)
    if existing:
        _, security, core, item = existing
        try:
            status = security.SecKeychainItemDelete(item)
        finally:
            core.CFRelease(item)
        if status != 0:
            raise CredentialError(f"macOS Keychain delete failed (status {status})")
        deleted = True
    for service_name in MACOS_LEGACY_SERVICES:
        result = _macos_security(
            ["delete-generic-password", "-a", account_name(), "-s", service_name]
        )
        if result.returncode == 0:
            deleted = True
            continue
        message = result.stderr.lower()
        if result.returncode != 44 and "could not be found" not in message:
            raise CredentialError("macOS Keychain could not delete the legacy AMap key")
    return deleted


def _windows_api() -> tuple[Any, Any, Any, Any]:
    if os.name != "nt":
        raise CredentialError(
            "Windows Credential Manager is unavailable on this platform"
        )

    import ctypes
    from ctypes import wintypes

    class CredentialW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    credential_pointer = ctypes.POINTER(CredentialW)
    api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    api.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(credential_pointer),
    ]
    api.CredReadW.restype = wintypes.BOOL
    api.CredWriteW.argtypes = [ctypes.POINTER(CredentialW), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    api.CredDeleteW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    return ctypes, wintypes, CredentialW, api


def _read_windows_target(target: str) -> str | None:
    ctypes, _, credential_type, api = _windows_api()
    pointer = ctypes.POINTER(credential_type)()
    if not api.CredReadW(target, 1, 0, ctypes.byref(pointer)):
        error_code = ctypes.get_last_error()
        if error_code == 1168:
            return None
        raise CredentialError(
            f"Windows Credential Manager could not read the AMap key (error {error_code})"
        )
    try:
        credential = pointer.contents
        raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return raw.decode("utf-16-le").rstrip("\x00") or None
    finally:
        api.CredFree(pointer)


def read_windows_credential() -> str | None:
    for target in (WINDOWS_TARGET, *WINDOWS_LEGACY_TARGETS):
        value = _read_windows_target(target)
        if value:
            return value
    return None


def write_windows_credential(key: str) -> None:
    ctypes, wintypes, credential_type, api = _windows_api()
    raw = key.encode("utf-16-le")
    if len(raw) > 2560:
        raise CredentialError("AMap key is too large for Windows Credential Manager")
    buffer = (wintypes.BYTE * len(raw)).from_buffer_copy(raw)
    credential = credential_type()
    credential.Type = 1
    credential.TargetName = WINDOWS_TARGET
    credential.CredentialBlobSize = len(raw)
    credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(wintypes.BYTE))
    credential.Persist = 2
    credential.UserName = account_name()
    if not api.CredWriteW(ctypes.byref(credential), 0):
        error_code = ctypes.get_last_error()
        raise CredentialError(
            f"Windows Credential Manager could not store the AMap key (error {error_code})"
        )


def delete_windows_credential() -> bool:
    ctypes, _, _, api = _windows_api()
    deleted = False
    for target in (WINDOWS_TARGET, *WINDOWS_LEGACY_TARGETS):
        if api.CredDeleteW(target, 1, 0):
            deleted = True
            continue
        error_code = ctypes.get_last_error()
        if error_code != 1168:
            raise CredentialError(
                f"Windows Credential Manager could not delete the AMap key (error {error_code})"
            )
    return deleted


def read_persistent_key() -> str | None:
    if sys.platform == "darwin":
        return read_macos_keychain()
    if os.name == "nt":
        return read_windows_credential()
    return None


def write_persistent_key(key: str) -> None:
    if sys.platform == "darwin":
        write_macos_keychain(key)
        return
    if os.name == "nt":
        write_windows_credential(key)
        return
    raise CredentialError(
        f"No native credential-store adapter for {sys.platform}; set {KEY_ENV} instead"
    )


def delete_persistent_key() -> bool:
    if sys.platform == "darwin":
        return delete_macos_keychain()
    if os.name == "nt":
        return delete_windows_credential()
    raise CredentialError(f"No native credential-store adapter for {sys.platform}")


def resolve_key() -> tuple[str, str]:
    environment_key = os.environ.get(KEY_ENV, "").strip()
    if environment_key:
        return environment_key, "environment"
    persistent_key = read_persistent_key()
    if persistent_key:
        return persistent_key, backend_name()
    if backend_name() == "environment-only":
        hint = f"set {KEY_ENV} in the environment"
    else:
        hint = "run scripts/amap_credentials.py set"
    raise CredentialError(f"AMap Web Service key is unavailable; {hint}")


def command_status() -> dict[str, Any]:
    environment_present = bool(os.environ.get(KEY_ENV, "").strip())
    persistent_present = bool(read_persistent_key())
    effective_source = (
        "environment"
        if environment_present
        else backend_name()
        if persistent_present
        else None
    )
    return {
        "key_present": environment_present or persistent_present,
        "effective_source": effective_source,
        "environment_present": environment_present,
        "persistent_backend": backend_name(),
        "persistent_credential_present": persistent_present,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Store or inspect an AMap key without printing the secret."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("set", help="Prompt for and save or update the key")
    subparsers.add_parser("status", help="Report whether a usable key exists")
    subparsers.add_parser("delete", help="Delete the native stored credential")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "set":
            key = getpass.getpass("AMap Web Service Key (input hidden): ").strip()
            if not key:
                raise CredentialError("empty key was not stored")
            write_persistent_key(key)
            result = {"stored": True, "backend": backend_name()}
        elif args.command == "delete":
            result = {"deleted": delete_persistent_key(), "backend": backend_name()}
        else:
            result = command_status()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {"error": str(error), "type": type(error).__name__}, ensure_ascii=False
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
