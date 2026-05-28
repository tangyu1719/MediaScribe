"""平台配置 —— 从 video_gui.py PLATFORMS 1:1 复制"""
from __future__ import annotations
from typing import Dict, Any, List

PLATFORM_CONFIGS: Dict[str, Dict[str, Any]] = {
    "小红书": {
        "api_endpoint": "https://www.hellotik.app/zh/rednote",
        "headers": {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json',
            'Referer': 'https://hellotik.app/',
            'Origin': 'https://hellotik.app'
        },
        "payload_template": {
            "requestURL": "{url}",
            "isMobile": "false",
            "isoCode": "HK",
            "adType": "adsense",
            "uwx_id": "uwx_350696y5juIO",
            "successCount": "0",
            "totalSuccessCount": "2",
            "firstSuccessDate": "2026-01-10",
            "time": "{timestamp}",
            "key": "xaq8pkc7"
        },
        "url_key_candidates": ["video_url", "download_url", "url"]
    },
    "抖音": {
        "api_endpoint": "https://api.douyin-downloader.com/api/download",
        "headers": {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json',
            'Referer': 'https://douyin-downloader.com/',
            'Origin': 'https://douyin-downloader.com'
        },
        "payload_template": {"url": "{url}", "isMobile": "false"},
        "url_key_candidates": ["video_url", "download_url", "url"]
    },
    "B站": {
        "api_endpoint": "https://api.bilibili-downloader.com/api/download",
        "headers": {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json',
            'Referer': 'https://bilibili-downloader.com/',
            'Origin': 'https://bilibili-downloader.com'
        },
        "payload_template": {"url": "{url}", "quality": "high"},
        "url_key_candidates": ["video_url", "download_url", "url"]
    }
}
