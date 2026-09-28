"""Acknowledged-send receipts for serial daily crypto report retries."""
import hashlib
import json
from pathlib import Path

from scripts.crypto_trend_market import _save


def send_once(scanner, text, output_dir, key, *, dry_run=False):
    if dry_run:
        return
    path = Path(output_dir)/'delivery_receipts'/(key+'.json')
    if path.exists():
        receipt = json.loads(path.read_text())
        if receipt.get('key') != key or receipt.get('status') != 'sent':
            raise ValueError('发送回执无效: '+key)
        if receipt.get('text_sha256') != hashlib.sha256(text.encode()).hexdigest():
            raise ValueError('已有发送回执的内容已改变，需显式更正发送: '+key)
        return
    if not scanner.send_telegram_alert(text):
        raise RuntimeError('发送失败: '+key)
    # Existing Quant resource lock serializes runs. The remote API has no
    # idempotency key: lost acknowledgments/crash before this write can duplicate.
    _save(path, dict(key=key,status='sent',text_sha256=hashlib.sha256(text.encode()).hexdigest()))
