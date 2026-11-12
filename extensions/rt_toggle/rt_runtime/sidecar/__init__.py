"""Sidecar host modules: lifecycle supervisor and loopback control transport.

The host itself is :mod:`rt_runtime.sidecar.host_stub`; the adapter resolves
``lifecycle`` and ``transport`` from this package before falling back to the
development work directory.
"""
