# SPDX-License-Identifier: MIT
"""Resolve network destinations from instance data, never from model guesses.

Device addresses come from the signed heartbeat's route towards this server.
Server addresses come from its default-route interface. Credential projection
contains only explicitly stored network metadata and is host-owner-only.
Unknown names retain ordinary DNS semantics; known but unusable/conflicting
internal names fail closed instead of silently addressing another machine.
"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
from pathlib import Path
import socket
import time


class NetworkTargetError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class NetworkTargetBinding:
    command_index: int
    name: str
    address: str
    sources: tuple[str, ...]


def _address(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if address.is_unspecified or address.is_multicast or address.is_loopback:
        return None
    # A remote link-local scope ID has no meaning on the server.
    if address.is_link_local or getattr(address, "scope_id", None) is not None:
        return None
    return str(address)


def _device_address(device: object, now: float) -> str | None:
    try:
        profile = json.loads(getattr(device, "profile_json", None) or "{}")
        observation = profile.get("network_observation", {})
        observed_at = observation.get("observed_at")
        if type(observed_at) is not int or not -30 <= now - observed_at <= 300:
            return None
        return _address(observation.get("address"))
    except (ValueError, TypeError, AttributeError):
        return None


def _server_addresses() -> set[str]:
    """The local IPv4 default-route interface, not loopback or every VPN."""
    import psutil

    routes = []
    for line in Path("/proc/net/route").read_text().splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 8 and fields[1] == "00000000" and fields[7] == "00000000":
            if int(fields[3], 16) & 1:
                routes.append((int(fields[6]), fields[0]))
    if not routes:
        return set()
    metric = min(row[0] for row in routes)
    interfaces = {iface for value, iface in routes if value == metric}
    addresses = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    return {
        address
        for iface in interfaces if iface in stats and stats[iface].isup
        for entry in addresses.get(iface, ()) if entry.family == socket.AF_INET
        if (address := _address(entry.address)) is not None
    }


def resolve_network_name(name: str, *, actor: str) -> tuple[str, tuple[str, ...]] | None:
    # IP literals are already authoritative, including intentional loopback.
    try:
        ipaddress.ip_address(name)
        return None
    except ValueError:
        pass

    import devices
    from target_device import _server_aliases

    owner = devices.owner_id_for_actor(actor)
    if owner == devices.NO_OWNER:
        return None
    alias = name.casefold()
    records: list[tuple[str, set[str]]] = []
    now = time.time()
    matched_devices = [
        device for device in devices.list_devices()
        if device.owner_user_id == owner
        and alias in {device.id.casefold(), device.name.casefold()}
    ]
    if len(matched_devices) > 1:
        raise NetworkTargetError("ERR_NETWORK_NAME_AMBIGUOUS")
    for device in matched_devices:
        address = _device_address(device, now)
        records.append(("device", {address} if address else set()))

    if alias in {value.casefold() for value in _server_aliases()}:
        if matched_devices:
            raise NetworkTargetError("ERR_NETWORK_NAME_AMBIGUOUS")
        try:
            addresses = _server_addresses()
        except (OSError, ValueError, ImportError):
            addresses = set()
        records.append(("server", addresses))

    # The credential vault belongs to the installation's host account. A
    # guest's paired device is not authority to read that account's metadata.
    if owner == devices.host_user_id():
        import credentials

        for addresses in credentials.network_addresses_for_name(name):
            records.append(("credential", addresses))

    if not records:
        return None
    addresses = set().union(*(values for _, values in records))
    if len(addresses) > 1:
        raise NetworkTargetError("ERR_NETWORK_NAME_AMBIGUOUS")
    if not addresses:
        raise NetworkTargetError("ERR_NETWORK_ADDRESS_UNAVAILABLE")
    return next(iter(addresses)), tuple(sorted({source for source, values in records if values}))


def bind_network_target(validated, *, actor: str):
    """Replace only the destination declared by the central command grammar."""
    from safety.canonicalize import declared_network_target_index, validate_argv

    index = declared_network_target_index(validated.signature.binary, validated.command_argv)
    if index is None:
        return validated, None
    name = validated.command_argv[index]
    resolved = resolve_network_name(name, actor=actor)
    if resolved is None:
        return validated, None
    address, sources = resolved
    if ((validated.signature.binary == "ping6" or "-6" in validated.command_argv)
            and ipaddress.ip_address(address).version != 6):
        raise NetworkTargetError("ERR_NETWORK_ADDRESS_UNAVAILABLE")
    if "-4" in validated.command_argv and ipaddress.ip_address(address).version != 4:
        raise NetworkTargetError("ERR_NETWORK_ADDRESS_UNAVAILABLE")
    argv = list(validated.argv)
    argv[len(argv) - len(validated.command_argv) + index] = address
    return validate_argv(argv), NetworkTargetBinding(index, name, address, sources)
