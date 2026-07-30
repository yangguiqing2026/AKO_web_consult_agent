# ============================================
# Author: AKO_studio
# Agent: AKO_web_consult_agent
# Generated: 2026-07-30
# ============================================
#
"""AKO 网站咨询网关 - 来源脱敏（内部文件名 → 对外显示名）"""

import json
import os
from src.config import settings

_DEFAULT_DISPLAY = "《阿格产品资料》"
_source_map: dict[str, str] = {}


def load_source_map() -> None:
    """启动时加载 source_map.json"""
    global _source_map
    map_path = os.path.join(settings.data_dir, "source_map.json")
    try:
        with open(map_path, "r", encoding="utf-8") as f:
            _source_map = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _source_map = {}


def mask(internal_source: str) -> str:
    """将内部文件名/编号转为对外显示名；未命中返回默认值"""
    return _source_map.get(internal_source, _DEFAULT_DISPLAY)