// GPU image enhancement: AMD FidelityFX CAS (Contrast Adaptive Sharpening),
// the sharpening stage FSR 1 builds on, applied while scaling the decoded
// video to the tile's on-screen size. The <video> keeps decoding underneath;
// a WebGL2 canvas draws the enhanced frame on top of it.

const VERTEX = `#version 300 es
out vec2 uv;
void main() {
  vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
  // Oversized triangle: p spans 0..2, so the visible square (clip -1..1) is
  // p 0..1. Video rows start at the top, clip space at the bottom.
  uv = vec2(p.x, 1.0 - p.y);
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`;

// CAS: the sharpening weight shrinks where the 3x3 neighbourhood already has
// strong contrast, so edges get crisper without ringing or noise blow-up.
const FRAGMENT = `#version 300 es
precision highp float;
in vec2 uv;
out vec4 color;
uniform sampler2D frame;
uniform vec2 texel;
uniform float sharpness;
vec3 tap(float x, float y) { return texture(frame, uv + vec2(x, y) * texel).rgb; }
void main() {
  vec3 a = tap(-1.0, -1.0), b = tap(0.0, -1.0), c = tap(1.0, -1.0);
  vec3 d = tap(-1.0, 0.0), e = tap(0.0, 0.0), f = tap(1.0, 0.0);
  vec3 g = tap(-1.0, 1.0), h = tap(0.0, 1.0), i = tap(1.0, 1.0);
  vec3 mn = min(min(min(d, e), min(f, b)), h);
  mn += min(mn, min(min(a, c), min(g, i)));
  vec3 mx = max(max(max(d, e), max(f, b)), h);
  mx += max(mx, max(max(a, c), max(g, i)));
  vec3 amp = sqrt(clamp(min(mn, 2.0 - mx) / max(mx, vec3(1e-5)), 0.0, 1.0));
  vec3 w = amp * (-1.0 / mix(8.0, 5.0, sharpness));
  vec3 outColor = (b * w + d * w + f * w + h * w + e) / (1.0 + 4.0 * w);
  color = vec4(clamp(outColor, 0.0, 1.0), 1.0);
}`;

const SHARPNESS = 0.6;

function compile(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
  return shader;
}

export function createEnhancer(video, host) {
  const canvas = document.createElement('canvas');
  canvas.className = 'enhanced';
  canvas.hidden = true;
  const gl = canvas.getContext('webgl2', { alpha: false, antialias: false, preserveDrawingBuffer: false });
  if (!gl) return null;
  const program = gl.createProgram();
  gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERTEX));
  gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return null;
  gl.useProgram(program);
  const texture = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, texture);
  // Mipmaps keep downscaling clean when the tile is smaller than the video.
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  const texelLocation = gl.getUniformLocation(program, 'texel');
  gl.uniform1f(gl.getUniformLocation(program, 'sharpness'), SHARPNESS);
  // Right after the video so the tile's labels and buttons stay on top.
  video.after(canvas);

  let enabled = false;
  let handle = null;

  // Match the canvas to the letterboxed video area at device-pixel resolution.
  const fit = () => {
    const box = host.getBoundingClientRect();
    if (!video.videoWidth || !box.width) return;
    const scale = Math.min(box.width / video.videoWidth, box.height / video.videoHeight);
    const width = video.videoWidth * scale;
    const height = video.videoHeight * scale;
    Object.assign(canvas.style, {
      width: `${width}px`,
      height: `${height}px`,
      left: `${(box.width - width) / 2}px`,
      top: `${(box.height - height) / 2}px`,
    });
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
  };
  const observer = new ResizeObserver(fit);
  observer.observe(host);

  const schedule = () => {
    handle = video.requestVideoFrameCallback
      ? video.requestVideoFrameCallback(draw)
      : requestAnimationFrame(draw);
  };
  function draw() {
    if (!enabled) return;
    if (video.readyState >= 2 && video.videoWidth) {
      if (!canvas.width) fit();
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, video);
      gl.generateMipmap(gl.TEXTURE_2D);
      // Sharpen at whichever is coarser, a video pixel or a screen pixel, so
      // the filter acts on detail that is actually visible at this size.
      gl.uniform2f(
        texelLocation,
        Math.max(1 / video.videoWidth, 1 / canvas.width),
        Math.max(1 / video.videoHeight, 1 / canvas.height),
      );
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    }
    schedule();
  }

  return {
    get enabled() { return enabled; },
    setEnabled(value) {
      enabled = value;
      canvas.hidden = !value;
      video.classList.toggle('is-under-canvas', value);
      if (value) { fit(); schedule(); }
    },
    destroy() {
      enabled = false;
      observer.disconnect();
      canvas.remove();
      video.classList.remove('is-under-canvas');
      gl.getExtension('WEBGL_lose_context')?.loseContext();
    },
  };
}
