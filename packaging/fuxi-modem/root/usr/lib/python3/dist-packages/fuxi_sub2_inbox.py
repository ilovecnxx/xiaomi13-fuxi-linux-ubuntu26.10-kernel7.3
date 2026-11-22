"""Read SIM2 transfer-route deliveries after authorization by the local helper."""

import json
import os
import re
import stat
from datetime import datetime, timedelta


SMS_FILE = '/var/log/fuxi-sim2-sms.jsonl'
MAX_LIMIT = 50
MAX_LINE = 2048
SCAN_BLOCK = 65536
GSM7 = ("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
        "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
EXTENSION = {10: '\f', 20: '^', 40: '{', 41: '}', 47: '\\', 60: '[', 61: '~',
             62: ']', 64: '|', 101: '€'}


class Unsupported(ValueError):
    pass


def unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError
    return result


def gsm_text(data, count, offset=0):
    if count < 0 or offset + count * 7 > len(data) * 8:
        raise ValueError
    packed = int.from_bytes(data, 'little') >> offset
    output = []
    escaped = False
    for _ in range(count):
        code = packed & 127
        packed >>= 7
        if escaped:
            if code not in EXTENSION:
                raise Unsupported
            output.append(EXTENSION[code])
            escaped = False
        elif code == 27:
            escaped = True
        else:
            output.append(GSM7[code])
    if escaped:
        raise ValueError
    return ''.join(output)


def sender_address(data, length, toa):
    if not 1 <= length <= 20 or not toa & 0x80 or len(data) != (length + 1) // 2:
        raise ValueError
    number_type = (toa >> 4) & 7
    if number_type == 5:
        count = length * 4 // 7
        if (count * 7 + 3) // 4 != length:
            raise ValueError
        return gsm_text(data, count)
    digits = [digit for octet in data for digit in (octet & 15, octet >> 4)]
    if (length % 2 and digits.pop() != 15) or 15 in digits:
        raise ValueError
    return ('+' if number_type == 1 else '') + ''.join('0123456789*#abc'[digit] for digit in digits)


def alphabet(dcs):
    if dcs < 0x80 and not dcs & 0x20:
        encoding = (dcs >> 2) & 3
        if encoding < 3:
            return ('gsm7', '8bit', 'ucs2')[encoding]
    if 0xc0 <= dcs < 0xf0 and not dcs & 4:
        return 'ucs2' if dcs >= 0xe0 else 'gsm7'
    if 0xf0 <= dcs < 0xf8:
        return '8bit' if dcs & 4 else 'gsm7'
    raise Unsupported


def user_header(data):
    if not data or data[0] + 1 > len(data):
        raise ValueError
    end = data[0] + 1
    offset = 1
    part = None
    extra = []
    while offset < end:
        if offset + 2 > end:
            raise ValueError
        kind, size = data[offset:offset + 2]
        offset += 2
        if offset + size > end:
            raise ValueError
        value = data[offset:offset + size]
        offset += size
        if kind in (0, 8):
            if part is not None or size != (3 if kind == 0 else 4):
                raise ValueError
            total, index = value[-2:]
            if not 1 <= index <= total:
                raise ValueError
            part = dict(reference=int.from_bytes(value[:-2], 'big'), reference_bits=(size - 2) * 8,
                        total=total, index=index)
        elif kind in (0x24, 0x25):
            raise Unsupported
        else:
            extra.append((kind, value))
    return end, part, tuple(extra)


def decode_deliver(pdu, envelope=False):
    if len(pdu) < 3:
        raise ValueError
    flags, length, toa = pdu[:3]
    if flags & 3:
        raise Unsupported
    address_end = 3 + (length + 1) // 2
    if len(pdu) < address_end + 10:
        raise ValueError
    sender = sender_address(pdu[3:address_end], length, toa)
    encoding = alphabet(pdu[address_end + 1])
    udl = pdu[address_end + 9]
    data = pdu[address_end + 10:]
    if len(data) > 140 or len(data) != ((udl * 7 + 7) // 8 if encoding == 'gsm7' else udl):
        raise ValueError
    header_size, part, extra = user_header(data) if flags & 0x40 else (0, None, ())
    result = dict(sender=sender, encoding=encoding, part=part)
    if envelope:
        result['envelope'] = (pdu[1:address_end + 2], extra)
    if encoding == 'gsm7':
        header_septets = (header_size * 8 + 6) // 7
        result['text'] = gsm_text(data, udl - header_septets, header_septets * 7)
    elif encoding == 'ucs2':
        result['text'] = data[header_size:].decode('utf-16-be')
    else:
        result['data_hex'] = data[header_size:].hex()
    return result


def record(offset, line, envelope=False):
    try:
        if len(line) > MAX_LINE:
            raise ValueError
        row = json.loads(line.decode('utf-8'), object_pairs_hook=unique_object)
        if (not isinstance(row, dict) or type(row.get('format')) is not int or
                type(row.get('txn')) is not int or not 0 <= row['txn'] <= 0xffffffff or
                not isinstance(row.get('t'), str) or
                not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}', row['t'])):
            raise ValueError
        if row['format'] != 6:
            raise Unsupported
        if not isinstance(row.get('pdu'), str) or not re.fullmatch(r'(?:[0-9a-fA-F]{2})+', row['pdu']):
            raise ValueError
        return dict(id=offset, received_at=row['t'], **decode_deliver(bytes.fromhex(row['pdu']), envelope))
    except Unsupported:
        return dict(id=offset, error='unsupported')
    except (ValueError, RecursionError):
        return dict(id=offset, error='malformed')


def read_page(cursor, limit):
    items = []
    more = False
    try:
        fd = os.open(SMS_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if cursor:
            raise ValueError
        return dict(items=items, next_cursor=0, more=False)
    with os.fdopen(fd, 'rb') as log:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise OSError
        if cursor > info.st_size:
            raise ValueError
        continuation = False
        if cursor:
            log.seek(cursor - 1)
            continuation = log.read(1) != b'\n'
            if continuation:
                if cursor % SCAN_BLOCK:
                    raise ValueError
                log.seek(cursor - MAX_LINE - 1)
                if b'\n' in log.read(MAX_LINE + 1):
                    raise ValueError
        end = min(info.st_size, (cursor // SCAN_BLOCK + 2) * SCAN_BLOCK)
        page = log.read(end - cursor)
        if len(page) != end - cursor:
            raise OSError
        start = 0
        while start < len(page) and len(items) < limit:
            newline = page.find(b'\n', start)
            if newline < 0:
                if end < info.st_size and (continuation or len(page) - start > MAX_LINE):
                    if not continuation:
                        items.append(dict(id=cursor + start, error='malformed'))
                    start = len(page)
                more = end < info.st_size
                break
            if not continuation:
                items.append(record(cursor + start, page[start:newline]))
            continuation = False
            start = newline + 1
        else:
            more = cursor + start < info.st_size
    return dict(items=items, next_cursor=cursor + start, more=more)


def run_inbox(stream):
    response = dict(version=1, slot=2)
    status = 0
    try:
        if os.geteuid() != 0:
            raise PermissionError
        raw = stream.read(257)
        if len(raw) > 256:
            raise ValueError
        request = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object)
        if (not isinstance(request, dict) or set(request) != {'version', 'cursor', 'limit'} or
                any(type(value) is not int for value in request.values()) or request['version'] != 1 or
                not 0 <= request['cursor'] < 2**63 or not 1 <= request['limit'] <= MAX_LIMIT):
            raise ValueError
        response.update(read_page(request['cursor'], request['limit']))
    except PermissionError:
        response['error'] = 'permission_denied'
        status = 1
    except (ValueError, RecursionError):
        response['error'] = 'invalid_request'
        status = 2
    except OSError:
        response['error'] = 'storage_unavailable'
        status = 1
    print(json.dumps(response, separators=(',', ':')))
    return status


def assemble_message(ids):
    fd = os.open(SMS_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as log:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise OSError
        chunks = {}
        identity = None
        times = []
        for offset in ids:
            if offset >= info.st_size:
                raise ValueError
            if offset:
                log.seek(offset - 1)
                if log.read(1) != b'\n':
                    raise ValueError
            else:
                log.seek(0)
            line = log.readline(MAX_LINE + 2)
            if not line.endswith(b'\n'):
                raise ValueError
            item = record(offset, line[:-1], envelope=True)
            part = item.get('part')
            if not part:
                raise ValueError
            key = (item['envelope'], part['reference_bits'], part['reference'], part['total'])
            if identity is None:
                identity = key
            if identity != key:
                return dict(result='conflict')
            times.append(datetime.fromisoformat(item['received_at']))
            field = 'data_hex' if item['encoding'] == '8bit' else 'text'
            index = part['index']
            if index in chunks and chunks[index] != item[field]:
                return dict(result='conflict')
            chunks[index] = item[field]
    if max(times) - min(times) > timedelta(hours=24):
        return dict(result='conflict')
    missing = [index for index in range(1, part['total'] + 1) if index not in chunks]
    if missing:
        return dict(result='incomplete', missing=missing)
    return dict(result='ok', sender=item['sender'], encoding=item['encoding'],
                received_at=min(times).isoformat(sep=' '), segments=part['total'],
                **{field: ''.join(chunks[index] for index in sorted(chunks))})


def run_message(stream):
    response = dict(version=1, slot=2)
    status = 0
    try:
        if os.geteuid() != 0:
            raise PermissionError
        raw = stream.read(12289)
        if len(raw) > 12288:
            raise ValueError
        request = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object)
        if not isinstance(request, dict) or set(request) != {'version', 'ids'}:
            raise ValueError
        ids = request['ids']
        if (type(request['version']) is not int or request['version'] != 1 or
                not isinstance(ids, list) or not 1 <= len(ids) <= 510 or
                any(type(offset) is not int or not 0 <= offset < 2**63 for offset in ids) or
                len(set(ids)) != len(ids)):
            raise ValueError
        response.update(assemble_message(ids))
    except PermissionError:
        response['error'] = 'permission_denied'
        status = 1
    except (ValueError, RecursionError):
        response['error'] = 'invalid_request'
        status = 2
    except OSError:
        response['error'] = 'storage_unavailable'
        status = 1
    print(json.dumps(response, separators=(',', ':')))
    return status
