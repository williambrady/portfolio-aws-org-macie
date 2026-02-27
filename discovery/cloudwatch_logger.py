#!/usr/bin/env python3
"""Streams stdin to CloudWatch Logs for real-time deployment monitoring.

Used as a background process by entrypoint.sh for every deployment.
Reads lines from stdin (connected via FIFO) and sends them to CloudWatch Logs
in small batches for near-real-time visibility into deployment progress.

Supports per-phase log streams via sentinel lines. When a line matching
``###STREAM:<stream-name>`` is received, the logger flushes any pending batch,
creates the new log stream, and switches to it. This allows each deployment
phase (bootstrap, discover, plan, etc.) to write to its own stream while
sharing a common timestamp prefix as the correlation key.

Best-effort: all exceptions are caught and suppressed to avoid disrupting
the deployment pipeline.
"""

import contextlib
import re
import sys
import time

import boto3

# Matches ANSI escape sequences (colors, cursor movement, etc.)
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")

# Sentinel prefix used to switch log streams mid-run
STREAM_SENTINEL = "###STREAM:"


def main():
    if len(sys.argv) != 4:
        print(
            "Usage: cloudwatch_logger.py <log-group> <initial-log-stream> <region>",
            file=sys.stderr,
        )
        sys.exit(1)

    log_group = sys.argv[1]
    current_stream = sys.argv[2]
    region = sys.argv[3]

    client = boto3.client("logs", region_name=region)

    batch = []
    batch_bytes = 0
    last_flush = time.time()

    try:
        while True:
            line = sys.stdin.readline()
            if not line:
                break

            stripped = line.rstrip("\n")

            # Check for stream-switch sentinel
            if stripped.startswith(STREAM_SENTINEL):
                if batch:
                    _flush(client, log_group, current_stream, batch)
                    batch = []
                    batch_bytes = 0
                    last_flush = time.time()
                new_stream = stripped.removeprefix(STREAM_SENTINEL)
                _create_stream(client, log_group, new_stream)
                current_stream = new_stream
                continue

            message = ANSI_ESCAPE_RE.sub("", stripped)
            if not message:
                continue

            # CloudWatch Logs max event size is 256KB
            encoded = message.encode("utf-8")
            if len(encoded) > 262144:
                message = message[:262000] + "... [truncated]"
                encoded = message.encode("utf-8")

            timestamp = int(time.time() * 1000)
            event = {"timestamp": timestamp, "message": message}
            event_bytes = len(encoded) + 26  # 26 bytes overhead per event

            batch.append(event)
            batch_bytes += event_bytes

            now = time.time()
            if len(batch) >= 10 or batch_bytes >= 800000 or (now - last_flush) >= 1:
                _flush(client, log_group, current_stream, batch)
                batch = []
                batch_bytes = 0
                last_flush = now

    except KeyboardInterrupt:
        pass
    except Exception:
        pass
    finally:
        if batch:
            _flush(client, log_group, current_stream, batch)


def _create_stream(client, log_group, log_stream):
    """Create a CloudWatch log stream. Best-effort, never raises."""
    with contextlib.suppress(Exception):
        client.create_log_stream(logGroupName=log_group, logStreamName=log_stream)


def _flush(client, log_group, log_stream, events):
    """Send a batch of events to CloudWatch Logs. Best-effort, never raises."""
    with contextlib.suppress(Exception):
        client.put_log_events(
            logGroupName=log_group,
            logStreamName=log_stream,
            logEvents=events,
        )


if __name__ == "__main__":
    main()
