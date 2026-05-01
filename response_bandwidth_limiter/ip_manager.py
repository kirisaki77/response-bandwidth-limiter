from ipaddress import ip_address

from .storage import Storage


class IPManager:
    def __init__(self, storage: Storage):
        self._storage = storage

    @property
    def storage(self) -> Storage:
        return self._storage

    async def block_ip(self, ip: str, duration: int | None = None) -> None:
        normalized_ip = self._normalize_ip(ip)
        await self._storage.set(f"ip:block:{normalized_ip}", "1", expire=duration)

    async def unblock_ip(self, ip: str) -> None:
        normalized_ip = self._normalize_ip(ip)
        await self._storage.delete(f"ip:block:{normalized_ip}")

    async def is_blocked(self, ip: str) -> bool:
        normalized_ip = self._normalize_ip(ip)
        return await self._storage.get(f"ip:block:{normalized_ip}") is not None

    async def allow_ip(self, ip: str) -> None:
        normalized_ip = self._normalize_ip(ip)
        await self._storage.set(f"ip:allow:{normalized_ip}", "1")

    async def remove_allow(self, ip: str) -> None:
        normalized_ip = self._normalize_ip(ip)
        await self._storage.delete(f"ip:allow:{normalized_ip}")

    async def is_allowed(self, ip: str) -> bool:
        normalized_ip = self._normalize_ip(ip)
        return await self._storage.get(f"ip:allow:{normalized_ip}") is not None

    def _normalize_ip(self, ip: str) -> str:
        try:
            return str(ip_address(ip))
        except ValueError as exc:
            raise ValueError("ip must be a valid IP address.") from exc