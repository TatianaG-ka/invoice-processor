"""Background job queue (Redis-backed via RQ).

Public API:

* :func:`app.queue.connection.get_redis` /
  :func:`app.queue.connection.get_queue` — lazy singletons driven by
  ``settings.REDIS_URL``.
* :func:`app.queue.connection.queue_dependency` — FastAPI dependency
  for routes that need to enqueue jobs (test-overridable).
* :func:`app.queue.tasks.process_pdf_invoice` — the job function that
  workers execute. Also invokable in-process when the queue is running
  in synchronous mode (tests).

Production workers connect to the same queue via::

    rq worker --url $REDIS_URL default

Phase 5 intentionally stores the raw PDF bytes as a job argument (RQ
serialises them into Redis via pickle). For portfolio-scale payloads
(<10 MB per file) this is fine; a production system with multi-MB
PDFs would swap the arg for a blob-store key.
"""
