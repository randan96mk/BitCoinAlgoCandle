import copy
import json
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).parent.parent / "config"
DEFAULT_CONFIG = CONFIG_DIR / "default.json"
USER_CONFIG = CONFIG_DIR / "user.json"

_MISSING = object()


class Config:
    """Config = default.json overlaid with user.json.

    user.json is a **sparse overlay** — it stores only the keys that actually
    differ from the current defaults. This means changes to default.json flow
    through on update instead of being permanently shadowed by a full snapshot,
    and on load any stale keys that now equal the default are pruned. Values the
    user genuinely changed to something other than the default are preserved.
    """

    _instance = None
    _data: dict = {}
    _defaults: dict = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self):
        with open(DEFAULT_CONFIG) as f:
            self._defaults = json.load(f)
        self._data = copy.deepcopy(self._defaults)
        if USER_CONFIG.exists():
            with open(USER_CONFIG) as f:
                try:
                    user = json.load(f)
                except (ValueError, TypeError):
                    user = {}
            self._deep_merge(self._data, user)
            # Heal an old full-snapshot user.json into a sparse overlay so keys
            # that now equal the defaults stop shadowing future updates.
            self._save_user()

    def _deep_merge(self, base: dict, override: dict):
        for k, v in override.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                self._deep_merge(base[k], v)
            else:
                base[k] = v

    def _diff(self, data: dict, defaults: dict) -> dict:
        """Keys in `data` that differ from `defaults` (recursively)."""
        out = {}
        for k, v in data.items():
            dv = defaults.get(k, _MISSING) if isinstance(defaults, dict) else _MISSING
            if isinstance(v, dict) and isinstance(dv, dict):
                sub = self._diff(v, dv)
                if sub:
                    out[k] = sub
            elif dv is _MISSING or v != dv:
                out[k] = v
        return out

    def get(self, dotpath: str, default: Any = None) -> Any:
        keys = dotpath.split(".")
        val = self._data
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val

    def set(self, dotpath: str, value: Any):
        keys = dotpath.split(".")
        d = self._data
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = value
        self._save_user()

    def _save_user(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        overlay = self._diff(self._data, self._defaults)
        with open(USER_CONFIG, "w") as f:
            json.dump(overlay, f, indent=2)

    def all(self) -> dict:
        return self._data.copy()

    def reload(self):
        self._load()
