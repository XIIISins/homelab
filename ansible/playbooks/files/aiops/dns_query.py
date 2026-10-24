#!/usr/bin/env python3
# ansible/playbooks/files/aiops/dns_query.py
"""Read-only DNS A lookups against ONE resolver, stdlib only (no dig on the guests or on Frigg).

    dns_query.py SERVER DOMAIN [DOMAIN ...]
    dns_query.py --compare SERVER_A SERVER_B DOMAIN [DOMAIN ...]

Prints one JSON object {domain: sorted list of IPv4 answers, or null on timeout/error}. With --compare it asks both
servers and prints {"equal": bool, "answered": domains that A answered, "mismatched": [domains]}; a domain A does not
answer is a mismatch, so a dead reference server can never read as parity. Used by the AIOps rebuild-verify
playbook to prove a rebuilt AdGuard replica answers (its own address, the VIP) and matches Saga (sync parity).
"""
import json
import random
import socket
import struct
import sys


def query(server: str, name: str, timeout: float = 2.0, tries: int = 2):
    qid = random.randrange(0, 65536)
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)  # recursion desired, one question
    qname = b"".join(bytes([len(p)]) + p.encode("ascii") for p in name.rstrip(".").split(".")) + b"\x00"
    packet = header + qname + struct.pack(">HH", 1, 1)  # A, IN
    for _ in range(tries):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        try:
            s.sendto(packet, (server, 53))
            data, _addr = s.recvfrom(4096)
        except OSError:
            continue
        finally:
            s.close()
        if len(data) < 12 or struct.unpack(">H", data[:2])[0] != qid:
            continue
        rcode = data[3] & 0x0F
        if rcode != 0:
            return None
        an = struct.unpack(">H", data[6:8])[0]
        pos = 12
        while data[pos] != 0:  # skip the question name
            pos += data[pos] + 1
        pos += 5  # root label + QTYPE + QCLASS
        ips = []
        for _i in range(an):
            if data[pos] & 0xC0 == 0xC0:  # compressed name
                pos += 2
            else:
                while data[pos] != 0:
                    pos += data[pos] + 1
                pos += 1
            rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", data[pos:pos + 10])
            pos += 10
            if rtype == 1 and rdlen == 4:
                ips.append(socket.inet_ntoa(data[pos:pos + 4]))
            pos += rdlen
        return sorted(ips)
    return None


def compare(a: str, b: str, domains: list) -> dict:
    answers_a = {d: query(a, d) for d in domains}
    answers_b = {d: query(b, d) for d in domains}
    mismatched = sorted(d for d in domains if answers_a[d] is None or answers_a[d] != answers_b[d])
    return {"equal": not mismatched, "answered": sum(1 for v in answers_a.values() if v), "mismatched": mismatched[:10]}


def main(argv: list) -> int:
    if len(argv) >= 5 and argv[1] == "--compare":
        print(json.dumps(compare(argv[2], argv[3], argv[4:]), sort_keys=True))
        return 0
    if len(argv) < 3:
        print("usage: dns_query.py SERVER DOMAIN [DOMAIN ...]", file=sys.stderr)
        return 2
    server, domains = argv[1], argv[2:]
    print(json.dumps({d: query(server, d) for d in domains}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
