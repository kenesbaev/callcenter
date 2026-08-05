from __future__ import annotations

import argparse
import asyncio
import os
import random
import re
import socket
import sys
import time
from dataclasses import dataclass


def header(message: str, name: str) -> str:
    match = re.search(
        rf"^{re.escape(name)}:\s*(.+)$", message, re.IGNORECASE | re.MULTILINE
    )
    return match.group(1).strip() if match else ""


def sdp_value(message: str, prefix: str) -> str:
    _, _, body = message.partition("\r\n\r\n")
    match = re.search(rf"^{re.escape(prefix)}(.+)$", body, re.MULTILINE)
    return match.group(1).strip() if match else ""


def rtp_packet(
    payload_type: int,
    sequence: int,
    ssrc: int,
    payload: bytes,
    *,
    timestamp: int | None = None,
    marker: bool = False,
) -> bytes:
    return (
        bytes((0x80, (0x80 if marker else 0) | (payload_type & 0x7F)))
        + (sequence & 0xFFFF).to_bytes(2, "big")
        + ((sequence * 160 if timestamp is None else timestamp) & 0xFFFFFFFF).to_bytes(
            4, "big"
        )
        + ssrc.to_bytes(4, "big")
        + payload
    )


def response(request: str, status: str, *, body: str = "", extra: str = "") -> bytes:
    via = header(request, "Via")
    from_value = header(request, "From")
    to_value = header(request, "To")
    if "tag=" not in to_value:
        to_value += ";tag=emulator"
    call_id = header(request, "Call-ID")
    cseq = header(request, "CSeq")
    lines = [
        f"SIP/2.0 {status}",
        f"Via: {via}",
        f"From: {from_value}",
        f"To: {to_value}",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq}",
        "Server: K-Line-Local-SIP-Emulator",
    ]
    if extra:
        lines.extend(extra.strip().splitlines())
    if body:
        lines.append("Content-Type: application/sdp")
    lines.append(f"Content-Length: {len(body.encode('utf-8'))}")
    return ("\r\n".join(lines) + "\r\n\r\n" + body).encode()


class SipServer(asyncio.DatagramProtocol):
    def __init__(self, media_port: int) -> None:
        self.transport: asyncio.DatagramTransport | None = None
        self.media_port = media_port

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, address: tuple[str, int]) -> None:
        message = data.decode("utf-8", errors="replace")
        method = message.split(" ", 1)[0]
        if method == "OPTIONS":
            self.send(response(message, "200 OK"), address)
            return
        if method == "BYE" or method == "CANCEL":
            self.send(response(message, "200 OK"), address)
            return
        if method != "INVITE":
            return
        scenario = header(message, "X-KLine-Test-Scenario").lower()
        if scenario == "busy":
            self.send(response(message, "486 Busy Here"), address)
            return
        if scenario == "rejected":
            self.send(response(message, "603 Decline"), address)
            return
        if scenario == "no-answer":
            self.send(response(message, "100 Trying"), address)
            self.send(response(message, "180 Ringing"), address)
            return
        self.send(response(message, "100 Trying"), address)
        self.send(response(message, "180 Ringing"), address)
        body = (
            "v=0\r\n"
            f"o=emulator 1 1 IN IP4 {address[0]}\r\n"
            "s=K-Line local media\r\n"
            f"c=IN IP4 {socket.gethostbyname(socket.gethostname())}\r\n"
            "t=0 0\r\n"
            f"m=audio {self.media_port} RTP/AVP 0 8 101\r\n"
            "a=rtpmap:0 PCMU/8000\r\n"
            "a=rtpmap:8 PCMA/8000\r\n"
            "a=rtpmap:101 telephone-event/8000\r\n"
            "a=fmtp:101 0-16\r\n"
            "a=sendrecv\r\n"
        )
        self.send(
            response(
                message,
                "200 OK",
                body=body,
                extra="Contact: <sip:emulator@127.0.0.1:5060>",
            ),
            address,
        )

    def send(self, data: bytes, address: tuple[str, int]) -> None:
        if self.transport:
            self.transport.sendto(data, address)


class RtpEcho(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.received = 0
        self.sent = 0
        self.dtmf: list[int] = []
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, address: tuple[str, int]) -> None:
        if len(data) < 12 or data[0] >> 6 != 2:
            return
        self.received += 1
        payload_type = data[1] & 0x7F
        if payload_type == 101 and len(data) > 12:
            self.dtmf.append(data[12])
        if self.transport:
            self.transport.sendto(data, address)
            self.sent += 1


async def serve() -> None:
    loop = asyncio.get_running_loop()
    media_port = int(os.environ.get("SIP_EMULATOR_RTP_PORT", "16000"))
    await loop.create_datagram_endpoint(
        lambda: SipServer(media_port), local_addr=("0.0.0.0", 5060)
    )
    await loop.create_datagram_endpoint(RtpEcho, local_addr=("0.0.0.0", media_port))
    print("local SIP/RTP emulator ready", flush=True)
    await asyncio.Future()


@dataclass
class UdpClient:
    sock: socket.socket
    target: tuple[str, int]

    def send(self, payload: str) -> None:
        self.sock.sendto(payload.encode(), self.target)

    def receive(self, timeout: float = 5.0) -> str:
        self.sock.settimeout(timeout)
        return self.sock.recvfrom(65535)[0].decode(errors="replace")


def inbound(target: str, did: str) -> int:
    host, port_text = target.rsplit(":", 1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))
    local_port = sock.getsockname()[1]
    rtp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rtp_sock.bind(("0.0.0.0", 0))
    rtp_port = int(rtp_sock.getsockname()[1])
    local_media_address = socket.gethostbyname(socket.gethostname())
    call_id = f"local-{random.getrandbits(64):x}@emulator"
    branch = f"z9hG4bK-{random.getrandbits(64):x}"
    tag = f"{random.getrandbits(32):x}"
    body = (
        "v=0\r\n"
        f"o=emulator 1 1 IN IP4 {local_media_address}\r\n"
        "s=K-Line inbound test\r\n"
        f"c=IN IP4 {local_media_address}\r\n"
        "t=0 0\r\n"
        f"m=audio {rtp_port} RTP/AVP 0 101\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=rtpmap:101 telephone-event/8000\r\n"
        "a=sendrecv\r\n"
    )
    invite = (
        f"INVITE sip:{did}@{host} SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP sip-emulator:{local_port};branch={branch};rport\r\n"
        f"From: <sip:+998900000001@sip-emulator>;tag={tag}\r\n"
        f"To: <sip:{did}@{host}>\r\n"
        f"Call-ID: {call_id}\r\n"
        "CSeq: 1 INVITE\r\n"
        f"Contact: <sip:emulator@sip-emulator:{local_port}>\r\n"
        "Max-Forwards: 10\r\n"
        "Content-Type: application/sdp\r\n"
        f"Content-Length: {len(body)}\r\n\r\n{body}"
    )
    client = UdpClient(sock, (host, int(port_text)))
    client.send(invite)
    messages: list[str] = []
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            message = client.receive(deadline - time.time())
        except TimeoutError:
            break
        messages.append(message.split("\r\n", 1)[0])
        if message.startswith("SIP/2.0 200"):
            to_value = header(message, "To")
            ack = (
                f"ACK sip:{did}@{host} SIP/2.0\r\nVia: SIP/2.0/UDP sip-emulator:{local_port};branch={branch}a\r\n"
                f"From: <sip:+998900000001@sip-emulator>;tag={tag}\r\nTo: {to_value}\r\n"
                f"Call-ID: {call_id}\r\nCSeq: 1 ACK\r\nContent-Length: 0\r\n\r\n"
            )
            client.send(ack)
            print("signaling verified", flush=True)
            connection = sdp_value(message, "c=").split()
            media = sdp_value(message, "m=audio ").split()
            if len(connection) < 3 or not media:
                print(
                    "audio failed: Asterisk response has no RTP endpoint",
                    file=sys.stderr,
                )
                return 1
            remote_rtp = (connection[2], int(media[0]))
            ssrc = random.getrandbits(32)
            packet_count = 50
            for sequence in range(packet_count):
                payload = bytes((0xFF if sequence % 2 == 0 else 0x7F,)) * 160
                rtp_sock.sendto(rtp_packet(0, sequence, ssrc, payload), remote_rtp)
                time.sleep(0.02)
            # RFC4733 digit 5 with a fixed event timestamp and three repeated
            # end packets as required for reliable receiver detection.
            dtmf_timestamp = packet_count * 160
            dtmf_packets = (
                (0x0A, 160, True),
                (0x0A, 320, False),
                (0x8A, 480, False),
                (0x8A, 480, False),
                (0x8A, 480, False),
            )
            for offset, (flags, duration, marker) in enumerate(dtmf_packets):
                rtp_sock.sendto(
                    rtp_packet(
                        101,
                        packet_count + offset,
                        ssrc,
                        bytes((5, flags)) + duration.to_bytes(2, "big"),
                        timestamp=dtmf_timestamp,
                        marker=marker,
                    ),
                    remote_rtp,
                )
                time.sleep(0.02)
            rtp_sock.settimeout(5.0)
            received = 0
            deadline = time.time() + 5
            while received < 5 and time.time() < deadline:
                try:
                    packet = rtp_sock.recvfrom(2048)[0]
                except TimeoutError:
                    break
                if len(packet) >= 12 and packet[0] >> 6 == 2:
                    received += 1
            if received < 5:
                print(
                    f"audio failed: received only {received} RTP packets from Asterisk",
                    file=sys.stderr,
                )
                return 1
            print(
                f"two-way RTP verified: sent={packet_count}, received={received}",
                flush=True,
            )
            bye_branch = f"z9hG4bK-bye-{random.getrandbits(32):x}"
            bye = (
                f"BYE sip:{did}@{host} SIP/2.0\r\n"
                f"Via: SIP/2.0/UDP sip-emulator:{local_port};branch={bye_branch};rport\r\n"
                f"From: <sip:+998900000001@sip-emulator>;tag={tag}\r\n"
                f"To: {to_value}\r\nCall-ID: {call_id}\r\nCSeq: 2 BYE\r\n"
                "Max-Forwards: 10\r\nContent-Length: 0\r\n\r\n"
            )
            client.send(bye)
            try:
                bye_response = client.receive(3.0)
            except TimeoutError:
                print("cleanup failed: BYE response timed out", file=sys.stderr)
                return 1
            if not bye_response.startswith("SIP/2.0 200"):
                print("cleanup failed: BYE was not accepted", file=sys.stderr)
                return 1
            print("DTMF sent and BYE verified", flush=True)
            return 0
        if message.startswith(("SIP/2.0 4", "SIP/2.0 5", "SIP/2.0 6")):
            break
    print("signaling failed: " + ", ".join(messages), file=sys.stderr)
    return 1


def health(target: str) -> int:
    host, port_text = target.rsplit(":", 1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))
    local_port = sock.getsockname()[1]
    branch = f"z9hG4bK-health-{random.getrandbits(32):x}"
    request = (
        f"OPTIONS sip:health@{host} SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP 127.0.0.1:{local_port};branch={branch};rport\r\n"
        "From: <sip:health@localhost>;tag=health\r\n"
        f"To: <sip:health@{host}>\r\n"
        f"Call-ID: health-{random.getrandbits(64):x}@localhost\r\n"
        "CSeq: 1 OPTIONS\r\n"
        "Max-Forwards: 1\r\n"
        "Content-Length: 0\r\n\r\n"
    )
    client = UdpClient(sock, (host, int(port_text)))
    client.send(request)
    try:
        reply = client.receive(2.0)
    except TimeoutError:
        return 1
    return 0 if reply.startswith("SIP/2.0 200") else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="mode", required=True)
    subparsers.add_parser("server")
    inbound_parser = subparsers.add_parser("inbound")
    inbound_parser.add_argument("--target", default="asterisk:5060")
    inbound_parser.add_argument("--did", required=True)
    health_parser = subparsers.add_parser("health")
    health_parser.add_argument("--target", default="127.0.0.1:5060")
    args = parser.parse_args()
    if args.mode == "server":
        asyncio.run(serve())
        return 0
    if args.mode == "health":
        return health(args.target)
    return inbound(args.target, args.did)


if __name__ == "__main__":
    raise SystemExit(main())
