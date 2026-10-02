"""Helpers for HyperHDR v22 smoothing config (config get/set)."""

from __future__ import annotations

import copy
import logging
from typing import Any

from hyperhdr import client

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_INSTANCE_CLIENTS,
    CONF_SMOOTHING_CONFIGS,
    DOMAIN,
    SIGNAL_SMOOTHING_CONFIG,
)


def _device_id(server_id: str, instance_num: int) -> str:
    return f"{server_id}_{instance_num}"


_LOGGER = logging.getLogger(__name__)


async def async_ensure_config_auth(
    hyperhdr_client: client.HyperHDRClient,
    *,
    admin_password: str | None,
) -> bool:
    """Ensure the client can call config get/set.

    Tries getconfig first. If authorization is required and an admin password
    is configured, logs in with that password and retries.
    """
    response = await hyperhdr_client.async_get_config()
    if client.ResponseOK(response):
        return True

    error = (response or {}).get("error") if isinstance(response, dict) else None
    needs_auth = isinstance(error, str) and "authorization" in error.lower()
    if not needs_auth or not admin_password:
        _LOGGER.debug(
            "HyperHDR config getconfig failed (auth_needed=%s, password_set=%s): %s",
            needs_auth,
            bool(admin_password),
            error,
        )
        return False

    login_resp = await hyperhdr_client.async_login(password=admin_password)
    if not client.LoginResponseOK(login_resp):
        _LOGGER.warning(
            "HyperHDR admin password login failed; smoothing config controls "
            "will be unavailable"
        )
        return False

    response = await hyperhdr_client.async_get_config()
    if client.ResponseOK(response):
        return True

    _LOGGER.warning(
        "HyperHDR config getconfig still failed after admin login: %s",
        (response or {}).get("error") if isinstance(response, dict) else None,
    )
    return False


async def async_load_smoothing_config(
    hyperhdr_client: client.HyperHDRClient,
    *,
    admin_password: str | None,
) -> dict[str, Any] | None:
    """Authenticate if needed and return the smoothing config object."""
    if not await async_ensure_config_auth(
        hyperhdr_client, admin_password=admin_password
    ):
        return None
    return await hyperhdr_client.async_get_smoothing_config()


async def async_patch_smoothing_config(
    hass: HomeAssistant,
    entry_id: str,
    server_id: str,
    instance_num: int,
    **fields: Any,
) -> bool:
    """Patch smoothing fields by rewriting the full instance config.

    HyperHDR's setconfig auto-corrects a partial config by re-defaulting every
    required section (leds, device, network, general), so only whole configs
    are sent.
    """
    entry_data = hass.data[DOMAIN][entry_id]
    hyperhdr_client: client.HyperHDRClient = entry_data[CONF_INSTANCE_CLIENTS][
        instance_num
    ]

    response = await hyperhdr_client.async_get_config()
    full = (response or {}).get("info") if client.ResponseOK(response) else None
    if not isinstance(full, dict) or not all(
        key in full for key in ("leds", "device", "network", "general", "smoothing")
    ):
        _LOGGER.warning(
            "Refusing smoothing write: getconfig did not return a full config"
        )
        return False

    full = copy.deepcopy(full)
    full["smoothing"] = {**full["smoothing"], **fields}

    if not client.ResponseOK(await hyperhdr_client.async_set_config(config=full)):
        _LOGGER.warning(
            "Failed to update HyperHDR smoothing config fields %s",
            list(fields),
        )
        return False

    updated = await hyperhdr_client.async_get_smoothing_config()
    if updated is None:
        updated = full["smoothing"]
    entry_data.setdefault(CONF_SMOOTHING_CONFIGS, {})[instance_num] = updated
    async_dispatcher_send(
        hass,
        SIGNAL_SMOOTHING_CONFIG.format(_device_id(server_id, instance_num)),
    )
    return True


def smoothing_config_available(entry_data: dict[str, Any], instance_num: int) -> bool:
    """Return True when smoothing config was loaded for this instance."""
    configs = entry_data.get(CONF_SMOOTHING_CONFIGS) or {}
    return instance_num in configs and configs[instance_num] is not None
