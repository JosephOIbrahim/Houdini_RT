"""Fast, TTL-cached GPU, NVIDIA driver, VRAM, and Houdini 22 OptiX cache telemetry."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import subprocess
import time
from typing import Any

# SideFX Houdini 22 Karma XPU recommended minimum NVIDIA driver on Windows
# (SideFX Houdini 22 docs: solaris/karma_xpu.md:24)
MIN_NVIDIA_DRIVER = 570.0
RECOMMENDED_WINDOWS_NVIDIA_DRIVER = 575.0
VRAM_WARN_PERCENT = 80.0
VRAM_CRITICAL_PERCENT = 90.0

_CACHE_TTL_SEC = 4.0
_CACHED_TS: float = 0.0
_CACHED_SNAPSHOT: HardwareSnapshot | None = None


@dataclass(frozen=True, slots=True)
class GpuDeviceInfo:
    name: str
    driver_version: str
    vram_used_mib: int
    vram_total_mib: int
    utilization_pct: int

    @property
    def vram_used_pct(self) -> float:
        if self.vram_total_mib <= 0:
            return 0.0
        return round((self.vram_used_mib / self.vram_total_mib) * 100.0, 1)

    @property
    def short_name(self) -> str:
        return self.name.replace("NVIDIA GeForce ", "").replace("NVIDIA ", "").strip()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["vram_used_pct"] = self.vram_used_pct
        d["short_name"] = self.short_name
        return d


@dataclass(frozen=True, slots=True)
class HardwareSnapshot:
    gpus: tuple[GpuDeviceInfo, ...]
    driver_version: str | None
    driver_version_num: float | None
    driver_ok_h22: bool | None
    vram_eviction_risk: str  # "OK" | "WARN" | "CRITICAL" | "UNKNOWN"
    optix_cache_dir: str
    optix_cache_ready: bool
    optix_cache_files: int
    summary_badge: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "gpus": [g.to_dict() for g in self.gpus],
            "driver_version": self.driver_version,
            "driver_version_num": self.driver_version_num,
            "driver_ok_h22": self.driver_ok_h22,
            "vram_eviction_risk": self.vram_eviction_risk,
            "optix_cache_dir": self.optix_cache_dir,
            "optix_cache_ready": self.optix_cache_ready,
            "optix_cache_files": self.optix_cache_files,
            "summary_badge": self.summary_badge,
        }


def _parse_driver_float(raw: str | None) -> float | None:
    if not raw:
        return None
    parts = str(raw).strip().split(".")
    try:
        if len(parts) >= 2:
            return float(f"{parts[0]}.{parts[1]}")
        return float(parts[0])
    except (TypeError, ValueError):
        return None


def _probe_nvidia_smi(timeout_sec: float = 1.5) -> tuple[GpuDeviceInfo, ...]:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            creationflags=creationflags,
            check=False,
        )
    except Exception:
        return ()

    if proc.returncode != 0 or not proc.stdout.strip():
        return ()

    devices: list[GpuDeviceInfo] = []
    for line in proc.stdout.strip().splitlines():
        cols = [c.strip() for c in line.split(",")]
        if len(cols) < 5:
            continue
        try:
            devices.append(
                GpuDeviceInfo(
                    name=cols[0],
                    driver_version=cols[1],
                    vram_used_mib=int(float(cols[2])),
                    vram_total_mib=int(float(cols[3])),
                    utilization_pct=int(float(cols[4])),
                )
            )
        except (TypeError, ValueError):
            continue
    return tuple(devices)


def _probe_optix_kernel_cache(major_minor: str = "22.0") -> tuple[str, bool, int]:
    """Inspect SideFX's Houdini 22 OptiX pre-compiled kernel cache directory."""
    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA", "")
        base = Path(local_app) / "NVIDIA" / "OptixCache" / f"Houdini{major_minor}" if local_app else Path("")
    else:
        user = os.environ.get("USER", "user")
        base = Path(f"/var/tmp/OptixCache_{user}") / f"Houdini{major_minor}"

    path_str = base.as_posix() if str(base) else ""
    if not path_str or not base.exists() or not base.is_dir():
        return path_str, False, 0
    try:
        files = [p for p in base.iterdir() if p.is_file() and p.stat().st_size > 0]
        return path_str, len(files) > 0, len(files)
    except Exception:
        return path_str, False, 0


def probe_hardware(*, force_refresh: bool = False, major_minor: str = "22.0") -> HardwareSnapshot:
    """Return live cached NVIDIA GPU, VRAM eviction risk, and OptiX kernel cache status."""
    global _CACHED_TS, _CACHED_SNAPSHOT
    now = time.monotonic()
    if not force_refresh and _CACHED_SNAPSHOT is not None and (now - _CACHED_TS) < _CACHE_TTL_SEC:
        return _CACHED_SNAPSHOT

    gpus = _probe_nvidia_smi()
    cache_dir, cache_ready, cache_files = _probe_optix_kernel_cache(major_minor=major_minor)

    driver_str = gpus[0].driver_version if gpus else None
    driver_num = _parse_driver_float(driver_str)
    driver_ok: bool | None = None
    if driver_num is not None:
        threshold = RECOMMENDED_WINDOWS_NVIDIA_DRIVER if os.name == "nt" else MIN_NVIDIA_DRIVER
        driver_ok = driver_num >= threshold

    if not gpus:
        risk = "UNKNOWN"
        badge = "CPU / No NVIDIA GPU detected"
    else:
        max_pct = max(g.vram_used_pct for g in gpus)
        if max_pct >= VRAM_CRITICAL_PERCENT:
            risk = "CRITICAL"
        elif max_pct >= VRAM_WARN_PERCENT:
            risk = "WARN"
        else:
            risk = "OK"
        primary = gpus[0]
        used_gb = primary.vram_used_mib / 1024.0
        total_gb = primary.vram_total_mib / 1024.0
        extra = f" +{len(gpus) - 1}" if len(gpus) > 1 else ""
        cache_tag = "OptiX:Warm" if cache_ready else "OptiX:Cold"
        badge = f"{primary.short_name}{extra} · {used_gb:.1f}/{total_gb:.0f}GB · Drv {driver_str} · {cache_tag}"

    snap = HardwareSnapshot(
        gpus=gpus,
        driver_version=driver_str,
        driver_version_num=driver_num,
        driver_ok_h22=driver_ok,
        vram_eviction_risk=risk,
        optix_cache_dir=cache_dir,
        optix_cache_ready=cache_ready,
        optix_cache_files=cache_files,
        summary_badge=badge,
    )
    _CACHED_TS = now
    _CACHED_SNAPSHOT = snap
    return snap


def clear_hardware_cache() -> None:
    global _CACHED_TS, _CACHED_SNAPSHOT
    _CACHED_TS = 0.0
    _CACHED_SNAPSHOT = None
