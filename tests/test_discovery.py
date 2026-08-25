from __future__ import annotations

import subprocess

import pytest

from cli_validator.discovery import (
    DiscoveryEngine,
    DiscoveryError,
    DiscoveryProvider,
    PCIeDiscoveryProvider,
)

LSPCI_OUTPUT = """0000:6a:00.0 Processing accelerators: Example Gen6 Device
\tLnkCap: Port #0, Speed 64GT/s, Width x16
\tLnkSta: Speed 32GT/s, Width x16
0000:af:00.0 Non-Volatile memory controller: Example Gen5 Device
\tLnkCap: Port #0, Speed 32GT/s, Width x4
"""


def test_pcie_discovery_finds_bdfs_generations_and_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IPSS_PLATFORM", "OKS")

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args[0], 0, LSPCI_OUTPUT, "")

    discovered = PCIeDiscoveryProvider(runner=runner).discover()

    assert discovered["platform"] == "OKS"
    assert discovered["pcie_devices"] == ["0000:6a:00.0", "0000:af:00.0"]
    assert discovered["gen6_devices"] == ["0000:6a:00.0"]
    assert discovered["gen5_devices"] == ["0000:af:00.0"]
    assert discovered["capabilities"]["pcie_gen6"] is True


def test_pcie_discovery_reports_command_failure() -> None:
    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args[0], 1, "", "lspci unavailable")

    with pytest.raises(DiscoveryError, match="lspci unavailable"):
        PCIeDiscoveryProvider(runner=runner).discover()


class BrokenProvider(DiscoveryProvider):
    def discover(self) -> dict[str, object]:
        raise RuntimeError("broken")


def test_discovery_engine_can_isolate_or_raise_plugin_failures() -> None:
    assert DiscoveryEngine([BrokenProvider()]).discover().devices == []
    with pytest.raises(DiscoveryError, match="BrokenProvider"):
        DiscoveryEngine([BrokenProvider()], strict=True).discover()


