from types import SimpleNamespace
import pytest
from scripts.crypto_report_delivery import send_once


def test_success_receipts_skip_resend_but_failure_allows_retry(tmp_path):
    sent=[]
    scanner=SimpleNamespace(send_telegram_alert=lambda text:sent.append(text) or text!='bad')
    send_once(scanner,'10d',tmp_path,'2026-09-27-trend-10d')
    with pytest.raises(RuntimeError):send_once(scanner,'bad',tmp_path,'2026-09-27-trend-14d')
    send_once(scanner,'10d',tmp_path,'2026-09-27-trend-10d')
    send_once(scanner,'14d',tmp_path,'2026-09-27-trend-14d')
    assert sent==['10d','bad','14d']


def test_dry_run_does_not_write_receipts_or_send(tmp_path):
    scanner=SimpleNamespace(send_telegram_alert=lambda text:pytest.fail('sent'))
    send_once(scanner,'preview',tmp_path,'test',dry_run=True)
    assert not list(tmp_path.rglob('*.json'))


def test_same_day_changed_content_is_not_silently_marked_delivered(tmp_path):
    sent=[]
    scanner=SimpleNamespace(send_telegram_alert=lambda text:sent.append(text) or True)
    send_once(scanner,'old',tmp_path,'2026-09-27-breadth-ok')
    with pytest.raises(ValueError,match='内容'):
        send_once(scanner,'corrected',tmp_path,'2026-09-27-breadth-ok')
    assert sent==['old']
