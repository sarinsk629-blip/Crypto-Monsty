/**
 * HFTGraphWebGL (placeholder engine)
 * Goal:
 * - Render millions of points at ~60 FPS (candles, spikes, overlays)
 * - Provide architecture for VBO batching, LOD downsampling, and overlays
 *
 * This is a high-performance placeholder with:
 * - WebGL2 init
 * - GPU buffers scaffold
 * - Frame loop with adaptive quality
 * - Fallback 2D mode
 */
export class HFTGraphWebGL {
  constructor(canvas, { maxPoints = 1_000_000 } = {}) {
    if (!canvas) throw new Error("HFTGraphWebGL requires a canvas");
    this.canvas = canvas;
    this.maxPoints = maxPoints;

    this.gl = canvas.getContext("webgl2", {
      antialias: false,
      alpha: false,
      depth: false,
      stencil: false,
      preserveDrawingBuffer: false,
      powerPreference: "high-performance"
    });

    this.ctx2d = null;
    if (!this.gl) this.ctx2d = canvas.getContext("2d");

    this.running = false;
    this.lastTs = 0;
    this.fps = 0;
    this.frameTimes = [];

    this.data = {
      candles: new Float32Array(0),   // [x,o,h,l,c,vol] repeated
      spikes: new Float32Array(0),    // [x,y,magnitude]
      overlays: new Map()             // name -> Float32Array
    };

    this.quality = {
      lod: 1, // 1 = full, 2 = half, 4 = quarter...
      targetFps: 60
    };

    this.gpu = {
      initialized: false,
      program: null,
      vao: null,
      buffers: new Map()
    };

    if (this.gl) this._initGL();
  }

  _initGL() {
    const gl = this.gl;
    // Minimal shader pair
    const vsSrc = `#version 300 es
    precision highp float;
    layout(location=0) in vec2 aPos;
    void main() {
      gl_Position = vec4(aPos, 0.0, 1.0);
      gl_PointSize = 1.0;
    }`;
    const fsSrc = `#version 300 es
    precision highp float;
    out vec4 fragColor;
    void main() {
      fragColor = vec4(0.10, 0.85, 0.95, 1.0);
    }`;

    const vs = this._compile(gl.VERTEX_SHADER, vsSrc);
    const fs = this._compile(gl.FRAGMENT_SHADER, fsSrc);
    const prog = gl.createProgram();
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      console.error("[HFTGraphWebGL] Shader link failed:", gl.getProgramInfoLog(prog));
      return;
    }

    const vao = gl.createVertexArray();
    gl.bindVertexArray(vao);

    const pointBuffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, pointBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, 8 * 1024 * 1024, gl.DYNAMIC_DRAW); // pre-alloc
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);

    this.gpu.program = prog;
    this.gpu.vao = vao;
    this.gpu.buffers.set("points", pointBuffer);
    this.gpu.initialized = true;
  }

  _compile(type, src) {
    const gl = this.gl;
    const sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
      console.error("[HFTGraphWebGL] Shader compile failed:", gl.getShaderInfoLog(sh));
    }
    return sh;
  }

  resize(width, height, dpr = (window.devicePixelRatio || 1)) {
    const w = Math.max(1, Math.floor(width * dpr));
    const h = Math.max(1, Math.floor(height * dpr));
    this.canvas.width = w;
    this.canvas.height = h;
    this.canvas.style.width = `${width}px`;
    this.canvas.style.height = `${height}px`;
    if (this.gl) this.gl.viewport(0, 0, w, h);
  }

  setCandles(floatArray) {
    this.data.candles = floatArray instanceof Float32Array ? floatArray : new Float32Array(floatArray || []);
  }

  setSpikes(floatArray) {
    this.data.spikes = floatArray instanceof Float32Array ? floatArray : new Float32Array(floatArray || []);
  }

  setOverlay(name, floatArray) {
    this.data.overlays.set(name, floatArray instanceof Float32Array ? floatArray : new Float32Array(floatArray || []));
  }

  start() {
    if (this.running) return;
    this.running = true;
    requestAnimationFrame(this._loop);
  }

  stop() {
    this.running = false;
  }

  _loop = (ts) => {
    if (!this.running) return;

    const dt = this.lastTs ? (ts - this.lastTs) : 16.7;
    this.lastTs = ts;

    this._trackFps(dt);
    this._adaptiveQuality();

    this.render(dt);
    requestAnimationFrame(this._loop);
  };

  _trackFps(dt) {
    const fps = dt > 0 ? 1000 / dt : 0;
    this.frameTimes.push(fps);
    if (this.frameTimes.length > 120) this.frameTimes.shift();
    this.fps = this.frameTimes.reduce((a, b) => a + b, 0) / this.frameTimes.length;
  }

  _adaptiveQuality() {
    // simple adaptive LOD
    if (this.fps < 45) this.quality.lod = Math.min(8, this.quality.lod * 2);
    else if (this.fps > 58) this.quality.lod = Math.max(1, this.quality.lod / 2);
  }

  render(_dt) {
    if (this.gl && this.gpu.initialized) {
      this._renderGL();
    } else if (this.ctx2d) {
      this._render2D();
    }
  }

  _renderGL() {
    const gl = this.gl;
    gl.clearColor(0.02, 0.04, 0.08, 1.0);
    gl.clear(gl.COLOR_BUFFER_BIT);

    const candles = this.data.candles;
    if (!candles.length) return;

    // Convert candle x/close into points for placeholder draw
    const step = this.quality.lod;
    const n = Math.min(this.maxPoints, Math.floor(candles.length / 6));
    const pts = new Float32Array(Math.ceil(n / step) * 2);

    let j = 0;
    for (let i = 0; i < n; i += step) {
      const base = i * 6;
      const x = candles[base + 0]; // expected normalized [-1..1]
      const c = candles[base + 4]; // expected normalized [-1..1]
      pts[j++] = x;
      pts[j++] = c;
    }

    gl.useProgram(this.gpu.program);
    gl.bindVertexArray(this.gpu.vao);

    const buf = this.gpu.buffers.get("points");
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, pts);

    gl.drawArrays(gl.POINTS, 0, pts.length / 2);
  }

  _render2D() {
    const ctx = this.ctx2d;
    const w = this.canvas.width;
    const h = this.canvas.height;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#05101f";
    ctx.fillRect(0, 0, w, h);

    const candles = this.data.candles;
    const n = Math.floor(candles.length / 6);
    if (!n) return;

    ctx.strokeStyle = "#33d1ff";
    ctx.lineWidth = 1;
    ctx.beginPath();

    const step = this.quality.lod;
    for (let i = 0; i < n; i += step) {
      const base = i * 6;
      const xn = candles[base + 0];
      const cn = candles[base + 4];
      const x = (xn * 0.5 + 0.5) * w;
      const y = (1 - (cn * 0.5 + 0.5)) * h;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();

    ctx.fillStyle = "#94a3b8";
    ctx.font = "12px monospace";
    ctx.fillText(`FPS: ${this.fps.toFixed(1)} | LOD: ${this.quality.lod}x`, 8, 16);
  }
}

export default HFTGraphWebGL;
