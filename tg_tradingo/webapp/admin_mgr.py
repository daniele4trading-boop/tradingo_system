"""Admin data manager: account settings, costs, withdrawals."""
from __future__ import annotations
import json
import threading
import time
import uuid
from pathlib import Path


class AdminManager:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"accounts": {}, "costs": [], "withdrawals": []}

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)

    def get_accounts(self) -> dict:
        with self._lock:
            return dict(self._data.get("accounts", {}))

    def set_account(self, terminal_id: str, cfg: dict) -> None:
        with self._lock:
            self._data.setdefault("accounts", {})[terminal_id] = {
                "label": cfg.get("label", ""),
                "type": cfg.get("type", "demo"),  # vetrina|personale|prop
                "public": bool(cfg.get("public", False)),
                "show_equity_total": bool(cfg.get("show_equity_total", False)),
            }
            self._save()

    def get_costs(self) -> list:
        with self._lock:
            return list(self._data.get("costs", []))

    def add_cost(self, item: dict) -> None:
        with self._lock:
            self._data.setdefault("costs", []).append({
                "id": str(uuid.uuid4())[:8],
                "date": item.get("date", time.strftime("%Y-%m")),
                "category": item.get("category", "altro"),  # vps|bruciato|prop|altro
                "amount": float(item.get("amount", 0)),
                "note": str(item.get("note", "")),
            })
            self._save()

    def delete_cost(self, cost_id: str) -> None:
        with self._lock:
            self._data["costs"] = [c for c in self._data.get("costs", []) if c.get("id") != cost_id]
            self._save()

    def get_withdrawals(self) -> list:
        with self._lock:
            return list(self._data.get("withdrawals", []))

    def add_withdrawal(self, item: dict) -> None:
        with self._lock:
            self._data.setdefault("withdrawals", []).append({
                "id": str(uuid.uuid4())[:8],
                "date": item.get("date", time.strftime("%Y-%m-%d")),
                "account": str(item.get("account", "")),
                "amount": float(item.get("amount", 0)),
                "note": str(item.get("note", "")),
            })
            self._save()

    def delete_withdrawal(self, w_id: str) -> None:
        with self._lock:
            self._data["withdrawals"] = [w for w in self._data.get("withdrawals", []) if w.get("id") != w_id]
            self._save()
