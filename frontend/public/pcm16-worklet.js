class AmipPcm16Processor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetSampleRate = 16000;
    this.samplesPerChunk = 1600;
    this.pendingSamples = [];
    this.position = 0;
    this.outputSamples = [];
    this.stopped = false;
    this.step = sampleRate / this.targetSampleRate;
    this.port.onmessage = (event) => {
      if (event.data?.type === "stop") {
        if (this.outputSamples.length >= 800) this.sendPcm(this.outputSamples);
        this.outputSamples = [];
        this.stopped = true;
        this.port.postMessage({ type: "stopped" });
      }
    };
  }

  sendPcm(samples) {
    const pcm16 = new Int16Array(samples.length);
    for (let index = 0; index < samples.length; index += 1) {
      const value = Math.max(-1, Math.min(1, samples[index]));
      pcm16[index] = value < 0 ? value * 0x8000 : value * 0x7fff;
    }
    this.port.postMessage({ type: "pcm16", buffer: pcm16.buffer }, [pcm16.buffer]);
  }

  process(inputs, outputs) {
    const output = outputs[0];
    if (output) for (const channel of output) channel.fill(0);
    if (this.stopped) return false;

    const input = inputs[0];
    if (!input || input.length === 0 || input[0].length === 0) return true;
    const frameCount = input[0].length;
    for (let frame = 0; frame < frameCount; frame += 1) {
      let mono = 0;
      for (const channel of input) mono += channel[frame] || 0;
      this.pendingSamples.push(mono / input.length);
    }

    while (this.position + 1 < this.pendingSamples.length) {
      const leftIndex = Math.floor(this.position);
      const fraction = this.position - leftIndex;
      const left = this.pendingSamples[leftIndex];
      const right = this.pendingSamples[leftIndex + 1];
      const sample = left + (right - left) * fraction;
      this.outputSamples.push(sample);
      this.position += this.step;

      if (this.outputSamples.length === this.samplesPerChunk) {
        this.sendPcm(this.outputSamples);
        this.outputSamples = [];
      }
    }

    const consumedSamples = Math.floor(this.position);
    if (consumedSamples > 0) {
      this.pendingSamples = this.pendingSamples.slice(consumedSamples);
      this.position -= consumedSamples;
    }
    return true;
  }
}

registerProcessor("amip-pcm16-processor", AmipPcm16Processor);
