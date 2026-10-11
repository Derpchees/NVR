// Camera microphone playback through Web Audio.
//
// The video element stays muted; sound goes band-pass (voice range, trims
// rumble and hiss) -> ducking -> output. Ducking silences the PTZ motor noise
// the built-in mic picks up while the camera moves.

const VOICE_LOW_HZ = 120;
const VOICE_HIGH_HZ = 3600;
const DUCK_RELEASE_MS = 600; // motors keep humming briefly after a stop

let context = null;

function audioContext() {
  context ||= new AudioContext();
  if (context.state === 'suspended') context.resume();
  return context;
}

export function createAudio(stream) {
  const tracks = stream.getAudioTracks();
  if (!tracks.length) return null;
  const ctx = audioContext();
  const source = ctx.createMediaStreamSource(new MediaStream(tracks));
  const highpass = new BiquadFilterNode(ctx, { type: 'highpass', frequency: VOICE_LOW_HZ });
  const lowpass = new BiquadFilterNode(ctx, { type: 'lowpass', frequency: VOICE_HIGH_HZ });
  const duck = new GainNode(ctx, { gain: 1 });
  const output = new GainNode(ctx, { gain: 0 });
  source.connect(highpass).connect(lowpass).connect(duck).connect(output).connect(ctx.destination);
  let duckTimer = null;

  return {
    setEnabled(value) {
      audioContext();
      output.gain.setTargetAtTime(value ? 1 : 0, ctx.currentTime, 0.05);
    },
    duck(active) {
      clearTimeout(duckTimer);
      if (active) duck.gain.setTargetAtTime(0, ctx.currentTime, 0.02);
      else duckTimer = setTimeout(() => duck.gain.setTargetAtTime(1, ctx.currentTime, 0.1), DUCK_RELEASE_MS);
    },
    destroy() {
      clearTimeout(duckTimer);
      source.disconnect();
      output.disconnect();
    },
  };
}
