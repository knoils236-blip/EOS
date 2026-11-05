"""Base class for EOS devices whose contract is an external SiLA 2 server."""

import asyncio
from typing import Any

from pydantic import model_validator

from eos.devices.base_device import BaseDevice
from eos.integrations.sila.sila_client_context import SilaClientContext
from eos.integrations.sila.sila_device_mixin import SilaDeviceMixin


class SilaDevice(BaseDevice, SilaDeviceMixin):
    """
    An instrument that runs its own SiLA 2 server. EOS holds only the endpoint; tasks call the instrument
    through ``SilaClientContext.connect(device, Client)``.

    Subclass with ``type="..."`` to register the instrument type. The connection comes from the lab
    definition: ``address`` and ``port``, or ``server_name`` for SiLA discovery.
    """

    class Config(BaseDevice.Config):
        address: str | None = None
        port: int | None = None
        server_name: str | None = None
        discovery_timeout: float = 5.0
        insecure: bool = False

        @model_validator(mode="after")
        def _check_endpoint(self) -> "SilaDevice.Config":
            if (self.address is None) != (self.port is None):
                raise ValueError("Set both 'address' and 'port', or use 'server_name' for discovery.")
            if self.address is None and self.server_name is None:
                raise ValueError("Set 'address' and 'port', or 'server_name' for discovery.")
            return self

    async def _initialize(self, config: Config) -> None:
        self.sila_add_server_connection(
            name="server",
            address=config.address,
            port=config.port,
            server_name=None if config.address else config.server_name,
            timeout=config.discovery_timeout,
            insecure=config.insecure,
        )
        # Fail lab loading now if the instrument is unreachable, instead of failing its first task
        from sila2.client import SilaClient  # noqa: PLC0415 (sila2 is an optional dependency)

        client = await SilaClientContext.create_client(self, SilaClient)
        try:
            self._sila_server_name = await asyncio.to_thread(client.SiLAService.ServerName.get)
        finally:
            client.close()

    async def _cleanup(self) -> None:
        pass

    async def _report(self) -> dict[str, Any]:
        return {"sila_server_name": self._sila_server_name, **self.sila_get_status()}
