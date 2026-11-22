"""Coordinate the initial audio route with the dual-subscription VOICE collector."""

import argparse
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import signal
import socket
from threading import Event

import fuxi_initial_audio as audio
from fuxi_voice_calls import IncompleteVoiceCalls, read_indicated_voice_calls
from fuxi_voice_session import CONVERSATION, END, LTE, VOICE_IP, select_subscription


DEBT = Path('/run/fuxi-call-owner/cleanup-required')
NEUTRAL = {'ready', 'awaiting-peers'}
RESIDUAL = {'active-sim1', 'active-sim2', 'cleanup'}
POLL_SECONDS = 0.5
IDLE_SECONDS = 2
MAX_RETRY_SECONDS = 30


def evidence():
    try:
        calls = read_indicated_voice_calls()
    except IncompleteVoiceCalls as error:
        known = [slot for slot, records in error.snapshot.items() if records is not None]
        if not known:
            return 'unknown-both', None
        slot, = known
        live = [record for record in error.snapshot[slot] if record[1] != END]
        if len(live) == 1 and live[0][1:] == (CONVERSATION, VOICE_IP, LTE, 0):
            return f'connected-sim{slot + 1}-other-unknown', None
        return f'unknown-sim{2 - slot}', None
    try:
        slot = select_subscription(calls)
    except ValueError:
        return 'ambiguous-or-ineligible', None
    if slot is None:
        return 'idle', None
    call_id, = [record[0] for record in calls[slot] if record[1] != END]
    return f'eligible-sim{slot + 1}', (slot, call_id)


def route_state():
    try:
        return audio.status()
    except (OSError, RuntimeError):
        return 'unreadable'


@contextmanager
def exclusive():
    with os.fdopen(os.open(audio.LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def notify(message):
    address = os.environ.get('NOTIFY_SOCKET')
    if address:
        if address.startswith('@'):
            address = '\0' + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
            client.sendto(message.encode('ascii'), address)


class Owner:
    def __init__(self, stop, debt):
        self.stop = stop
        self.debt = debt
        self.call = None
        self.retry_key = None
        self.delay = POLL_SECONDS

    def poll(self, delay=POLL_SECONDS):
        self.retry_key = None
        self.delay = delay

    def backoff(self, key):
        self.delay = min(MAX_RETRY_SECONDS, 2 * self.delay) if self.retry_key == key else 2
        self.retry_key = key

    def release(self):
        self.call = None
        self.debt.touch(mode=0o600, exist_ok=True)
        try:
            audio.submit('stop')
        except OSError:
            self.backoff('stop')
            return 'cleanup-pending'
        if route_state() not in NEUTRAL:
            self.backoff('stop')
            return 'cleanup-pending'
        self.debt.unlink()
        if self.retry_key == 'stop':
            self.poll()
        return 'released'

    def failed_start(self, target):
        result = self.release()
        if result == 'released':
            self.backoff(target)
        return result

    def step(self):
        route = route_state()
        if self.call is None and (self.debt.exists() or route in RESIDUAL):
            return self.release()
        if self.call is not None and route != f'active-sim{self.call[0] + 1}':
            return f'route={route} {self.release()}'
        if self.call is None and route != 'ready':
            self.poll(IDLE_SECONDS)
            return f'calls=not-polled route={route}'
        observed, target = evidence()
        route = route_state()
        if self.call is not None:
            if (self.call != target or self.stop.is_set()
                    or route != f'active-sim{self.call[0] + 1}'):
                return f'calls={observed} route={route} {self.release()}'
            return f'calls={observed} route={route}'
        if target is None or self.stop.is_set() or route != 'ready':
            self.poll()
            return f'calls={observed} route={route}'

        self.debt.touch(mode=0o600, exist_ok=True)
        expected = f'active-sim{target[0] + 1}'
        try:
            audio.submit(f'start-sim{target[0] + 1}')
        except OSError:
            return f'start-failed {self.failed_start(target)}'
        if self.stop.is_set() or route_state() != expected:
            return f'start-unconfirmed {self.failed_start(target)}'
        observed, current = evidence()
        route = route_state()
        if current != target or self.stop.is_set():
            self.poll()
            return f'calls={observed} route={route} {self.release()}'
        if route != expected:
            return f'start-unconfirmed {self.failed_start(target)}'
        self.call = target
        self.poll()
        return f'calls={observed} route={route}'


def run(stop, debt):
    owner = Owner(stop, debt)
    previous = None
    with exclusive():
        try:
            notify('READY=1\nSTATUS=Monitoring VOICE and initial_voice_v1')
            while not stop.is_set():
                current = owner.step()
                if current != previous:
                    notify('STATUS=' + current)
                    previous = current
                stop.wait(owner.delay)
        finally:
            if (debt.exists() or route_state() in RESIDUAL) and owner.release() != 'released':
                raise RuntimeError('initial voice cleanup pending')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('run', 'status', 'cleanup'))
    action = parser.parse_args().action
    if action == 'status':
        observed, target = evidence()
        route = route_state()
        candidate = route == 'ready' and target is not None and not DEBT.exists()
        print(f'calls={observed} route={route} start_candidate={int(candidate)}')
        return
    stop = Event()
    if action == 'cleanup':
        if DEBT.exists():
            with exclusive():
                if DEBT.exists() and Owner(stop, DEBT).release() != 'released':
                    raise RuntimeError('initial voice cleanup pending')
        return
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    run(stop, DEBT)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError) as error:
        raise SystemExit(str(error)) from None
