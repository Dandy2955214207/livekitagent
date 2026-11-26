class MicrophoneCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.samplesPerOutput = sampleRate / 16000;
    this.remaining = this.samplesPerOutput;
    this.sum = 0;
    this.offset = 1;
    this.packet = this.createPacket();
  }

  createPacket() {
    const packet = new DataView(new ArrayBuffer(641));
    packet.setUint8(0, 1);
    return packet;
  }

  process(inputs) {
    for (const input of inputs[0][0] ?? []) {
      let weight = 1;
      while (weight > 1e-8) {
        const part = Math.min(weight, this.remaining);
        this.sum += input * part;
        this.remaining -= part;
        weight -= part;
        if (this.remaining > 1e-8) continue;

        const value = Math.max(-1, Math.min(1, this.sum / this.samplesPerOutput));
        this.packet.setInt16(this.offset, Math.round(value * (value < 0 ? 32768 : 32767)), true);
        this.remaining = this.samplesPerOutput;
        this.sum = 0;
        this.offset += 2;
        if (this.offset === this.packet.byteLength) {
          this.port.postMessage(this.packet.buffer, [this.packet.buffer]);
          this.packet = this.createPacket();
          this.offset = 1;
        }
      }
    }
    return true;
  }
}

registerProcessor('trial-microphone', MicrophoneCapture);
