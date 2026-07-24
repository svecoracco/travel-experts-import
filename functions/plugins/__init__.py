"""Plugin-registry — poort van
`travel-experts-backend/apps/main/app/plugins/__init__.py` (1-op-1, zuivere
Python, geen Odoo-toegang).
"""

from __future__ import annotations

from typing import Dict, List

from plugins.base import ImportPlugin, PluginMeta

_registry: Dict[str, ImportPlugin] = {}


def register_plugin(plugin: ImportPlugin) -> None:
    meta = plugin.get_meta()
    _registry[meta.name] = plugin


def get_plugin(name: str) -> ImportPlugin:
    if name not in _registry:
        raise KeyError(f"Unknown plugin: {name}")
    return _registry[name]


def list_plugins() -> List[PluginMeta]:
    return [p.get_meta() for p in _registry.values()]


def discover_plugins() -> None:
    """Auto-discover en registreer alle 8 ingebouwde plugins."""
    from plugins.airplus.plugin import AirplusPlugin
    from plugins.bsp.plugin import BspPlugin
    from plugins.commission.plugin import CommissionPlugin
    from plugins.divers.plugin import DiversPlugin
    from plugins.ibanfirst.plugin import IbanFirstPlugin
    from plugins.rail.plugin import RailPlugin
    from plugins.tui.plugin import TuiPlugin
    from plugins.vivawallet.plugin import VivawalletPlugin

    register_plugin(AirplusPlugin())
    register_plugin(BspPlugin())
    register_plugin(CommissionPlugin())
    register_plugin(DiversPlugin())
    register_plugin(IbanFirstPlugin())
    register_plugin(RailPlugin())
    register_plugin(TuiPlugin())
    register_plugin(VivawalletPlugin())
