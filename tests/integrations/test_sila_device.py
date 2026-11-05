import socket

import pytest
from pydantic import ValidationError

pytest.importorskip("sila2")

from eos.devices.base_device import DeviceStatus
from eos.devices.exceptions import EosDeviceInitializationError
from eos.integrations.sila import SilaClientContext, SilaDevice
from sila2.client import SilaClient
from sila2.server import SilaServer


class Medusa(SilaDevice, type="medusa"):
    """Robotic synthesis platform with its own SiLA 2 server."""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def sila_server():
    server = SilaServer(
        server_name="MedusaServer",
        server_type="TestServer",
        server_description="Test instrument",
        server_version="0.1",
        server_vendor_url="https://example.org",
    )
    port = free_port()
    server.start_insecure("127.0.0.1", port, enable_discovery=False)
    yield port
    server.stop()


class TestSilaDevice:
    async def test_initializes_against_running_server(self, sila_server):
        device = Medusa("medusa", "lab")

        await device.initialize({"address": "127.0.0.1", "port": sila_server, "insecure": True})

        assert device.status == DeviceStatus.IDLE
        assert (await device.report())["sila_server_name"] == "MedusaServer"
        async with SilaClientContext.connect(device, SilaClient) as client:
            assert client.SiLAService.ServerName.get() == "MedusaServer"
        await device.cleanup()

    async def test_unreachable_server_fails_initialization(self):
        device = Medusa("medusa", "lab")

        with pytest.raises(EosDeviceInitializationError):
            await device.initialize({"address": "127.0.0.1", "port": free_port(), "insecure": True})
        assert device.status == DeviceStatus.ERROR

    @pytest.mark.parametrize("config", [{}, {"address": "127.0.0.1"}, {"port": 50051}])
    def test_config_requires_endpoint(self, config):
        with pytest.raises(ValidationError):
            Medusa.Config.model_validate(config)

    def test_config_accepts_discovery(self):
        assert Medusa.Config.model_validate({"server_name": "MedusaServer"}).server_name == "MedusaServer"
