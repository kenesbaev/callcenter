export const OPENAI_PCM_RATE = 24_000;
export const TELEPHONY_PCM_RATE = 8_000;

export function pcmuRtpToPcm24k(packet: Uint8Array): Int16Array {
  if (packet.byteLength < 12 || packet[0]! >> 6 !== 2)
    throw new Error("invalid_rtp_packet");
  if ((packet[1]! & 0x7f) !== 0) throw new Error("rtp_codec_mismatch");
  const source = packet.subarray(rtpHeaderLength(packet));
  const pcm8k = Int16Array.from(source, decodeMuLaw);
  return resample8kTo24k(pcm8k);
}

export function pcm24kToPcmuPayload(pcmBytes: Uint8Array): Uint8Array {
  if (pcmBytes.byteLength % 2 !== 0)
    throw new Error("pcm_frame_alignment_invalid");
  const view = new DataView(
    pcmBytes.buffer,
    pcmBytes.byteOffset,
    pcmBytes.byteLength,
  );
  const pcm24k = new Int16Array(pcmBytes.byteLength / 2);
  for (let index = 0; index < pcm24k.length; index += 1)
    pcm24k[index] = view.getInt16(index * 2, true);
  return Uint8Array.from(resample24kTo8k(pcm24k), encodeMuLaw);
}

export function pcm16ToBytes(samples: Int16Array): Uint8Array {
  const bytes = new Uint8Array(samples.length * 2);
  const view = new DataView(bytes.buffer);
  samples.forEach((sample, index) => view.setInt16(index * 2, sample, true));
  return bytes;
}

export function buildPcmuRtpPacket(
  payload: Uint8Array,
  sequence: number,
  timestamp: number,
  ssrc: number,
): Buffer {
  const packet = Buffer.alloc(12 + payload.byteLength);
  packet[0] = 0x80;
  packet[1] = 0;
  packet.writeUInt16BE(sequence & 0xffff, 2);
  packet.writeUInt32BE(timestamp >>> 0, 4);
  packet.writeUInt32BE(ssrc >>> 0, 8);
  Buffer.from(payload).copy(packet, 12);
  return packet;
}

function rtpHeaderLength(packet: Uint8Array): number {
  const csrcCount = packet[0]! & 0x0f;
  let offset = 12 + csrcCount * 4;
  if ((packet[0]! & 0x10) !== 0) {
    if (packet.byteLength < offset + 4)
      throw new Error("invalid_rtp_extension");
    const words = (packet[offset + 2]! << 8) | packet[offset + 3]!;
    offset += 4 + words * 4;
  }
  if (offset >= packet.byteLength) throw new Error("invalid_rtp_payload");
  return offset;
}

function resample8kTo24k(input: Int16Array): Int16Array {
  const output = new Int16Array(input.length * 3);
  for (let index = 0; index < input.length; index += 1) {
    const current = input[index]!;
    const next = input[Math.min(index + 1, input.length - 1)]!;
    output[index * 3] = current;
    output[index * 3 + 1] = Math.round((current * 2) / 3 + next / 3);
    output[index * 3 + 2] = Math.round(current / 3 + (next * 2) / 3);
  }
  return output;
}

function resample24kTo8k(input: Int16Array): Int16Array {
  const output = new Int16Array(Math.floor(input.length / 3));
  for (let index = 0; index < output.length; index += 1) {
    output[index] = Math.round(
      (input[index * 3]! + input[index * 3 + 1]! + input[index * 3 + 2]!) / 3,
    );
  }
  return output;
}

function decodeMuLaw(value: number): number {
  const inverted = ~value & 0xff;
  const sign = inverted & 0x80;
  const exponent = (inverted >> 4) & 0x07;
  const mantissa = inverted & 0x0f;
  const magnitude = ((mantissa << 3) + 0x84) << exponent;
  const sample = magnitude - 0x84;
  return sign ? -sample : sample;
}

function encodeMuLaw(sample: number): number {
  const bias = 0x84;
  const clipped = Math.max(-32635, Math.min(32635, sample));
  const sign = clipped < 0 ? 0x80 : 0;
  let magnitude = Math.abs(clipped) + bias;
  let exponent = 7;
  for (let mask = 0x4000; exponent > 0 && (magnitude & mask) === 0; mask >>= 1)
    exponent -= 1;
  const mantissa = (magnitude >> (exponent + 3)) & 0x0f;
  magnitude = ~(sign | (exponent << 4) | mantissa);
  return magnitude & 0xff;
}
