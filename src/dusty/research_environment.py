"""Non-secret runtime provenance; collecting it never initializes an MT5 connection."""
from importlib.metadata import PackageNotFoundError, version
import platform
import sys

from .features import FEATURE_NUMERICS_VERSION


def runtime_provenance() -> dict[str, object]:
    packages = {}
    for name in ("dusty-dragon-reasoning", "MetaTrader5", "numpy"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = "not_installed"
    return {
        "python": platform.python_version(), "implementation": platform.python_implementation(),
        "os": platform.system(), "os_release": platform.release(), "machine": platform.machine(),
        "packages": packages, "feature_numerics": FEATURE_NUMERICS_VERSION,
        "float_radix": sys.float_info.radix, "float_mantissa_bits": sys.float_info.mant_dig,
    }
