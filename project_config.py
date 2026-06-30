# project_config.py
import json
import os
from pathlib import Path
from typing import List, Dict

class ProjectConfigManager:
    """
    Manage lightweight app config for DB-backed projects.
    Stores recent project NAMES (not file paths) and last selected project name.
    """

    def __init__(self):
        self.config_dir = Path.home() / ".system_architecture"
        self.config_file = self.config_dir / "recent_projects.json"
        self.max_recent = 10
        self._ensure_config_dir()

    def _ensure_config_dir(self):
        self.config_dir.mkdir(exist_ok=True)

    def _default_config(self) -> Dict:
        return {
            "recent_projects": [],  # list of str: project names
            "last_project_name": None,
            # legacy fields (ignored but preserved if present)
            "recent_file_projects": [],  # legacy
            "last_directory": str(Path.home()),  # legacy (unused in DB mode)
        }

    def _load_config(self) -> Dict:
        if not self.config_file.exists():
            return self._default_config()
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return self._default_config()

        # Migration from legacy, if needed
        # If "recent_projects" seems empty but "recent_file_projects" exists,
        # attempt to extract names from filenames.
        if not data.get("recent_projects") and data.get("recent_file_projects"):
            names = []
            for p in data.get("recent_file_projects", []):
                try:
                    base = os.path.basename(p)
                    name, _ = os.path.splitext(base)
                    if name and name not in names:
                        names.append(name)
                except Exception:
                    continue
            data["recent_projects"] = names[: self.max_recent]
        return data

    def _save_config(self, cfg: Dict) -> None:
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        except Exception:
            pass  # best-effort only

    # ------------------------------
    # Public API for DB-backed usage
    # ------------------------------
    def add_recent_project(self, project_name: str) -> None:
        if not project_name:
            return
        cfg = self._load_config()
        recent: List[str] = cfg.get("recent_projects", [])
        if project_name in recent:
            recent.remove(project_name)
        recent.insert(0, project_name)
        recent = recent[: self.max_recent]
        cfg["recent_projects"] = recent
        cfg["last_project_name"] = project_name
        self._save_config(cfg)

    def get_recent_projects(self) -> List[str]:
        cfg = self._load_config()
        recent: List[str] = cfg.get("recent_projects", [])
        # filter out accidental empties
        return [n for n in recent if isinstance(n, str) and n.strip()]

    def clear_recent_projects(self) -> None:
        cfg = self._load_config()
        cfg["recent_projects"] = []
        self._save_config(cfg)

    def get_last_project_name(self) -> str:
        cfg = self._load_config()
        val = cfg.get("last_project_name")
        if isinstance(val, str) and val.strip():
            return val
        return ""

    def set_last_project_name(self, project_name: str) -> None:
        cfg = self._load_config()
        cfg["last_project_name"] = project_name or None
        self._save_config(cfg)

    # ------------------------------
    # Legacy API (kept to avoid breaks)
    # ------------------------------
    def get_last_directory(self) -> str:
        # Not used in DB-backed mode; kept for backward compatibility
        cfg = self._load_config()
        last_dir = cfg.get("last_directory", str(Path.home()))
        if not os.path.exists(last_dir):
            last_dir = str(Path.home())
        return last_dir

    def set_last_directory(self, directory: str) -> None:
        cfg = self._load_config()
        cfg["last_directory"] = str(directory)
        self._save_config(cfg)

    def remove_recent_project(self, project_identifier: str) -> None:
        # Accept either old file path or new project name
        cfg = self._load_config()
        recent: List[str] = cfg.get("recent_projects", [])
        # Attempt direct removal
        if project_identifier in recent:
            recent.remove(project_identifier)
        else:
            # If a path is passed, try to map to a name
            try:
                name = os.path.splitext(os.path.basename(project_identifier))[0]
                if name in recent:
                    recent.remove(name)
            except Exception:
                pass
        cfg["recent_projects"] = recent
        self._save_config(cfg)

# Global instance
project_config = ProjectConfigManager()
