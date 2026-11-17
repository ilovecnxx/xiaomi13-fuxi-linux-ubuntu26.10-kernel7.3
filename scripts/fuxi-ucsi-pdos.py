#!/usr/bin/env python3
"""Read partner source PDOs, validating reply length with UCSI event filters.

Run as root with no other debugfs UCSI command writer. Only GET_CAPABILITY and
GET_PDOS are issued; normal kernel ACKs still occur. This does not select a PDO.
"""

import argparse
from contextlib import ExitStack, contextmanager
import fcntl
import json
import os
from pathlib import Path
import re


TRACE = Path('/sys/kernel/tracing/instances')
DEBUG = Path('/sys/kernel/debug/usb/ucsi/pmic_glink.ucsi.0')
EVENT = 'events/ucsi/ucsi_run_command'
LENGTHS = (0, 4, 8, 12, 16)


def response_bytes(text):
    text = text.strip()
    if not re.fullmatch(r'0x[0-9a-fA-F]{48}', text):
        raise ValueError('Expected a 24-byte UCSI debugfs response')
    return int(text, 16).to_bytes(24, 'little')


def event_count(text):
    return sum(' ucsi_run_command:' in line for line in text.splitlines()
               if not line.startswith('#'))


def verified_length(traces, stats):
    if not stats:
        raise ValueError('Missing per-CPU loss statistics')
    for text in stats:
        values = dict(re.findall(r'^(overrun|commit overrun|dropped events):\s*(\d+)$',
                                 text, re.MULTILINE))
        if len(values) != 3 or any(int(v) for v in values.values()):
            raise ValueError('Incomplete or nonzero trace loss statistics')
    if event_count(traces['all']) != 1:
        raise ValueError('Expected exactly one command completion')
    counts = {n: event_count(traces[str(n)]) for n in LENGTHS}
    matches = [n for n, count in counts.items() if count == 1]
    if len(matches) != 1 or sum(counts.values()) != 1:
        raise ValueError('Reply length is ambiguous or outside supported lengths')
    return matches[0]


def source_command(connector, offset, count):
    if not 1 <= connector <= 127 or not 0 <= offset <= 7 or not 1 <= count <= 4:
        raise ValueError('Invalid GET_PDOS parameters')
    return (0x10 | connector << 16 | 1 << 23 | offset << 24 |
            (count - 1) << 32 | 1 << 34)


def decode_pdo(value):
    if not value:
        raise ValueError('Zero PDO inside the reported reply length')
    kind = value >> 30
    result = {'raw': f'0x{value:08x}', 'type': kind}
    if kind == 0:
        result.update(kind='fixed', voltage_mv=((value >> 10) & 1023) * 50,
                      max_current_ma=(value & 1023) * 10)
    elif kind == 1:
        result.update(kind='battery', min_voltage_mv=((value >> 10) & 1023) * 50,
                      max_voltage_mv=((value >> 20) & 1023) * 50,
                      max_power_mw=(value & 1023) * 250)
    elif kind == 2:
        result.update(kind='variable', min_voltage_mv=((value >> 10) & 1023) * 50,
                      max_voltage_mv=((value >> 20) & 1023) * 50,
                      max_current_ma=(value & 1023) * 10)
    elif (value >> 28) & 3 == 0:
        result.update(kind='pps', min_voltage_mv=((value >> 8) & 255) * 100,
                      max_voltage_mv=((value >> 17) & 255) * 100,
                      max_current_ma=(value & 127) * 50)
    else:
        result['kind'] = 'unsupported_apdo'
    return result


@contextmanager
def trace_instance(path):
    path.mkdir()
    try:
        (path / 'tracing_on').write_text('0')
        (path / 'buffer_size_kb').write_text('64')
        yield path
    finally:
        try:
            (path / 'tracing_on').write_text('0')
            (path / EVENT / 'enable').write_text('0')
        finally:
            path.rmdir()


class Reader:
    def __init__(self, instances, output):
        self.instances = instances
        self.output = output
        self.index = 0

    def query(self, command, max_length):
        self.index += 1
        evidence = self.output / f'query-{self.index}'
        evidence.mkdir()
        for key, path in self.instances.items():
            expression = f'common_pid == {os.getpid()} && ctrl == {command}'
            if key != 'all':
                expression += f' && ret == {key}'
            (path / EVENT / 'filter').write_text(expression)
            (evidence / f'filter-{key}.txt').write_text(
                (path / EVENT / 'filter').read_text())
            (path / 'trace').write_text('')
            (path / EVENT / 'enable').write_text('1')
        try:
            for path in self.instances.values():
                (path / 'tracing_on').write_text('1')
            (DEBUG / 'command').write_text(f'0x{command:x}\n')
            response = (DEBUG / 'response').read_text()
        finally:
            for path in self.instances.values():
                (path / 'tracing_on').write_text('0')
        traces, stats = {}, []
        cpu_sets = []
        for key, path in self.instances.items():
            traces[key] = (path / 'trace').read_text()
            (evidence / f'trace-{key}.txt').write_text(traces[key])
            cpu_files = sorted((path / 'per_cpu').glob('cpu*/stats'))
            cpu_sets.append({s.parent.name for s in cpu_files})
            for source in cpu_files:
                text = source.read_text()
                stats.append(text)
                (evidence / f'{key}-{source.parent.name}-stats.txt').write_text(text)
        if not cpu_sets[0] or any(c != cpu_sets[0] for c in cpu_sets):
            raise ValueError('Incomplete CPU statistics across trace instances')
        (evidence / 'response.txt').write_text(response)
        length = verified_length(traces, stats)
        (evidence / 'query.json').write_text(json.dumps({
            'command': f'0x{command:x}', 'response_length': length,
            'requested_length': max_length}, indent=2) + '\n')
        if length > max_length:
            raise ValueError('Response exceeds the requested length')
        return response_bytes(response)[:length]


def read_source(reader, connector):
    capability = reader.query(0x6, 16)
    if len(capability) != 16 or connector > (capability[4] & 127):
        raise ValueError('Invalid capability length or connector number')
    features = int.from_bytes(capability[5:7], 'little')
    first = reader.query(source_command(connector, 0, 4), 16)
    data = first
    if len(first) == 16:
        data += reader.query(source_command(connector, 4, 3), 12)
    pdos = [decode_pdo(int.from_bytes(data[n:n + 4], 'little'))
            for n in range(0, len(data), 4)]
    return {'result': 'capabilities_read', 'connector': connector,
            'pdo_details_advertised': bool(features & 16), 'pdos': pdos,
            'active_contract_verified': False, 'fast_charge_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connector', type=int, default=1)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.connector <= 127:
        parser.error('connector must be 1..127')
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'result': 'insufficient_evidence'}
    try:
        with open('/run/lock/fuxi-ucsi-pdos.lock', 'a') as lock, ExitStack() as stack:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            (args.output / 'event-format.txt').write_text(
                (TRACE.parent / EVENT / 'format').read_text())
            instances = {
                key: stack.enter_context(trace_instance(
                    TRACE / f'fuxi-pdos-{os.getpid()}-{key}'))
                for key in ('all', *(str(n) for n in LENGTHS))}
            report = read_source(Reader(instances, args.output), args.connector)
    except (OSError, ValueError) as error:
        report['error'] = str(error)
    (args.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['result'] == 'capabilities_read' else 1


if __name__ == '__main__':
    raise SystemExit(main())
