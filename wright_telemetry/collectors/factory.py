"""Registry-based factory for miner collector adapters.

Usage:
    @CollectorFactory.register("braiins")
    class BraiinsCollector(MinerCollector): ...

    collector = CollectorFactory.create("braiins", url="http://192.168.1.100", ...)
"""

from __future__ import annotations

from typing import Optional, Type

from wright_telemetry.collectors.base import MinerCollector


class CollectorFactory:
    _registry: dict[str, Type[MinerCollector]] = {}

    @classmethod
    def register(cls, name: str):
        """Decorator that registers a collector class under *name*."""
        def decorator(klass: Type[MinerCollector]) -> Type[MinerCollector]:
            cls._registry[name.lower()] = klass
            return klass
        return decorator

    @classmethod
    def create(
        cls,
        name: str,
        url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> MinerCollector:
        klass = cls._registry.get(name.lower())
        if klass is None:
            available = ", ".join(sorted(cls._registry)) or "(none)"
            raise ValueError(f"Unknown collector type '{name}'. Available: {available}")
        return klass(url=url, username=username, password=password)

    @classmethod
    def available(cls) -> list[str]:
        return sorted(cls._registry)
