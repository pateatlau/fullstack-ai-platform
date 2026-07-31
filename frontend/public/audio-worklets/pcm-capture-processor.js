class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super()
    this.frameSize = options.processorOptions?.frameSize ?? 2048
    this.buffer = new Float32Array(this.frameSize)
    this.writeIndex = 0
  }

  process(inputs) {
    const input = inputs[0]?.[0]
    if (!input?.length) return true

    let readIndex = 0
    while (readIndex < input.length) {
      const remaining = this.frameSize - this.writeIndex
      const toCopy = Math.min(remaining, input.length - readIndex)
      this.buffer.set(input.subarray(readIndex, readIndex + toCopy), this.writeIndex)
      this.writeIndex += toCopy
      readIndex += toCopy

      if (this.writeIndex === this.frameSize) {
        this.port.postMessage(this.buffer, [this.buffer.buffer])
        this.buffer = new Float32Array(this.frameSize)
        this.writeIndex = 0
      }
    }
    return true
  }
}

registerProcessor('pcm-capture-processor', PcmCaptureProcessor)
