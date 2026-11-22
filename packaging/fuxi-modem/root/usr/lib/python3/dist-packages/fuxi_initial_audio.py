"""Access the kernel's initial VoLTE route and its single-writer lock."""

from pathlib import Path


ENDPOINT = Path('/sys/bus/aprbus/devices/gprsvc:service:2:1/initial_voice_v1')
LOCK = '/run/fuxi-initial-voice.lock'
STATES = {'unavailable', 'awaiting-peers', 'ready', 'active-sim1', 'active-sim2', 'cleanup'}


def status():
    state = ENDPOINT.read_text(encoding='ascii').rstrip('\n')
    if state not in STATES:
        raise RuntimeError('unknown initial voice ABI state')
    return state


def submit(command):
    if command not in {'stop', 'start-sim1', 'start-sim2'}:
        raise ValueError('unknown initial voice command')
    payload = (command + '\n').encode('ascii')
    with ENDPOINT.open('wb', buffering=0) as endpoint:
        if endpoint.write(payload) != len(payload):
            raise OSError('incomplete route submission')
