class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super()
    this.frameSize = options.processorOptions?.frameSize ?? 2048
    this.buffer = new Float32Array(this.frameSize)
    this.writeIndex = 0
    this.activeGeneration = 0
    this.capturing = false

    this.port.onmessage = (event) => {
      const msg = event.data
      if (msg.type === 'start') {
        this.activeGeneration = msg.generation
        this.capturing = true
        this.buffer = new Float32Array(this.frameSize)
        this.writeIndex = 0
      } else if (msg.type === 'stop') {
        this.capturing = false
        this.flushPartial(msg.generation)
      }
    }
  }

  flushPartial(generation) {
    if (this.writeIndex > 0) {
      const samples = this.buffer.subarray(0, this.writeIndex)
      const flushed = new Float32Array(samples)
      this.port.postMessage({ type: 'flush', generation, samples: flushed }, [flushed.buffer])
    } else {
      this.port.postMessage({ type: 'flush', generation, samples: new Float32Array(0) })
    }
    this.buffer = new Float32Array(this.frameSize)
    this.writeIndex = 0
  }

  process(inputs) {
    if (!this.capturing) return true

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
        const frame = this.buffer
        this.port.postMessage(
          { type: 'frame', generation: this.activeGeneration, samples: frame },
          [frame.buffer],
        )
        this.buffer = new Float32Array(this.frameSize)
        this.writeIndex = 0
      }
    }
    return true
  }
}

registerProcessor('pcm-capture-processor', PcmCaptureProcessor)
