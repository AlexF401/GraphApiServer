from datetime import timedelta

from app.graph import GraphError
from app.store import utcnow

PAYLOAD = {"to": ["someone@example.com"], "subject": "Hi", "body": "x"}


def test_retryable_error_backs_off_then_fails_after_max_attempts(store, worker, mailer):
    record = store.enqueue("billing", PAYLOAD)
    mailer.errors += [GraphError("Graph returned 503", 503, retryable=True)] * 3

    # Attempt 1 fails -> queued again 10s later (base 10s).
    assert worker.run_once()
    r = store.get(record.id)
    assert (r.status, r.attempts) == ("queued", 1)
    assert 9 <= (r.next_attempt_at - utcnow()).total_seconds() <= 11
    assert worker.run_once() is False  # not due yet

    # Attempt 2 -> 20s backoff.
    worker.process(store.claim_due(now=utcnow() + timedelta(seconds=11)))
    r = store.get(record.id)
    assert (r.status, r.attempts) == ("queued", 2)
    assert 19 <= (r.next_attempt_at - utcnow()).total_seconds() <= 21

    # Attempt 3 is the last (max_attempts=3) -> failed.
    worker.process(store.claim_due(now=utcnow() + timedelta(seconds=30)))
    r = store.get(record.id)
    assert (r.status, r.attempts) == ("failed", 3)
    assert "503" in r.last_error


def test_retry_after_header_is_respected(store, worker, mailer):
    record = store.enqueue("billing", PAYLOAD)
    mailer.errors.append(GraphError("Graph returned 429", 429, retryable=True, retry_after=60))
    worker.run_once()
    r = store.get(record.id)
    assert 59 <= (r.next_attempt_at - utcnow()).total_seconds() <= 61


def test_backoff_is_capped(worker):
    assert worker.backoff_seconds(10, None) == 100


def test_non_retryable_error_fails_immediately(store, worker, mailer):
    record = store.enqueue("billing", PAYLOAD)
    mailer.errors.append(GraphError("Graph returned 400: bad", 400, retryable=False))
    worker.run_once()
    assert store.get(record.id).status == "failed"


def test_burst_is_sent_in_order(store, worker, mailer):
    ids = [store.enqueue("billing", {**PAYLOAD, "subject": f"n{i}"}).id for i in range(100)]
    while worker.run_once():
        pass
    assert [e.subject for e in mailer.sent] == [f"n{i}" for i in range(100)]
    assert all(store.get(i).status == "sent" for i in ids)


def test_interrupted_sends_are_recovered(store, worker, mailer):
    record = store.enqueue("billing", PAYLOAD)
    store.claim_due()  # simulate a crash mid-send
    assert store.get(record.id).status == "sending"
    assert store.recover_interrupted() == 1
    worker.run_once()
    assert store.get(record.id).status == "sent"


def test_queue_survives_restart(tmp_path):
    from app.store import EmailStore

    path = str(tmp_path / "q.db")
    s1 = EmailStore(path)
    rid = s1.enqueue("billing", PAYLOAD).id
    s1.close()
    s2 = EmailStore(path)
    assert s2.get(rid).status == "queued"
    s2.close()


def test_worker_thread_drains_queue(store, mailer, settings):
    import time

    from app.worker import QueueWorker

    settings = settings.model_copy(update={"send_interval_seconds": 0.0, "worker_poll_seconds": 0.01})
    w = QueueWorker(store, mailer, settings)
    for _ in range(5):
        store.enqueue("billing", PAYLOAD)
    w.start()
    deadline = time.time() + 5
    while len(mailer.sent) < 5 and time.time() < deadline:
        time.sleep(0.02)
    w.stop()
    assert len(mailer.sent) == 5
