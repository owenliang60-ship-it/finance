"""Private notifications. Queued in SQLite first; delivery never changes trading state."""


def enqueue(rt, text, key=None):
    """Queue once per key. Without a key every call queues a new message."""
    key = key or 'n:%.3f:%d' % (rt.clock(), len(rt.store.evidence('notification')))
    rt.store.add_evidence('notification', key, {'text': text}, status='pending')


def flush(rt):
    """Send what is pending. A failed send stays pending for the next run."""
    sent = failures = 0
    for row in rt.store.pending_notifications()[:20]:
        try:
            delivered = bool(rt.send(row['payload']['text']))
        except Exception:
            delivered = False
        if not delivered:
            failures += 1
            if failures >= 2:   # the channel is probably down; do not hold the lock retrying
                break
            continue            # one bad message must not block the alerts behind it
        rt.store.mark_notification_sent(row['id'])
        sent += 1
    return sent
