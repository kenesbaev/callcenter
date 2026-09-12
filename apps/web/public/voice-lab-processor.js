/* global AudioWorkletProcessor, sampleRate, registerProcessor */

class VoiceLabCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetRate = 24000;
    this.ratio = sampleRate / this.targetRate;
    this.source = [];
    this.readPosition = 0;
    this.frame = new Int16Array(960);
    this.framePosition = 0;
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input || input.length === 0) return true;
    for (let index = 0; index < input.length; index += 1) {
      this.source.push(input[index]);
    }
    while (this.readPosition + 1 < this.source.length) {
      const lower = Math.floor(this.readPosition);
      const fraction = this.readPosition - lower;
      const sample =
        this.source[lower] * (1 - fraction) + this.source[lower + 1] * fraction;
      const clamped = Math.max(-1, Math.min(1, sample));
      this.frame[this.framePosition] =
        clamped < 0 ? clamped * 32768 : clamped * 32767;
      this.framePosition += 1;
      this.readPosition += this.ratio;
      if (this.framePosition === this.frame.length) {
        const ready = this.frame;
        this.port.postMessage(ready.buffer, [ready.buffer]);
        this.frame = new Int16Array(960);
        this.framePosition = 0;
      }
    }
    const consumed = Math.floor(this.readPosition);
    if (consumed > 0) {
      this.source.splice(0, consumed);
      this.readPosition -= consumed;
    }
    return true;
  }
}

registerProcessor("voice-lab-capture", VoiceLabCaptureProcessor);
