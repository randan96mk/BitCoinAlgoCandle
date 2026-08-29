import json
import os
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).parent.parent / "config"
DEFAULT_CONFIG = CONFIG_DIR / "default.json"
USER_CONFIG = CONFIG_DIR / "user.json"


class Config:
    _instance = None
    _data: dict = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self):
        with open(DEFAULT_CONFIG) as f:
            self._data = json.load(f)
        if USER_CONFIG.exists():
            with open(USER_CONFIG) as f:
                user = json.load(f)
            self._deep_merge(self._data, user)

    def _deep_merge(self, base: dict, override: dict):
        for k, v in override.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                self._deep_merge(base[k], v)
            else:
                base[k] = v

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
        with open(USER_CONFIG, "w") as f:
            json.dump(self._data, f, indent=2)

    def all(self) -> dict:
        return self._data.copy()

    def reload(self):
        self._load()
