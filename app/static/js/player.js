// Live playback: WebRTC (WHEP) first for sub-second latency, HLS as fallback.

import { api } from './api.js';

export function reportEvent(cameraId, video, event, detail = '', fatal = false, latency = null) {
  void api(`/api/cameras/${cameraId}/playback-events`, {
    method: 'POST',
    body: {
      event,
      detail: String(detail).slice(0, 120),
      fatal,
      media_time: Number.isFinite(video.currentTime) ? video.currentTime : null,
      latency: Number.isFinite(latency) ? latency : null,
    },
  }).catch(() => {});
}

function waitFor(promise, milliseconds, message) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(message)), milliseconds);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

function iceGatheringComplete(connection) {
  if (connection.iceGatheringState === 'complete') return Promise.resolve();
  return new Promise((resolve) => {
    connection.addEventListener('icegatheringstatechange', () => {
      if (connection.iceGatheringState === 'complete') resolve();
    });
  });
}

function peerConnected(connection) {
  return new Promise((resolve, reject) => {
    const check = () => {
      if (connection.connectionState === 'connected') resolve();
      if (connection.connectionState === 'failed') reject(new Error('WebRTC: conexión fallida'));
    };
    connection.addEventListener('connectionstatechange', check);
    check();
  });
}

async function startWebRTC(endpoint, video, onLost) {
  const connection = new RTCPeerConnection();
  let location = null;
  const session = {
    mode: 'WebRTC',
    destroy() {
      connection.close();
      video.srcObject = null;
      if (location) fetch(location, { method: 'DELETE' }).catch(() => {});
    },
  };
  try {
    connection.addTransceiver('video', { direction: 'recvonly' });
    // Microphone audio (G.711/Opus) when the camera sends it; WebRTC plays G.711 natively.
    connection.addTransceiver('audio', { direction: 'recvonly' });
    const stream = new MediaStream();
    video.srcObject = stream;
    const tracked = new Promise((resolve) => {
      connection.addEventListener('track', (event) => {
        stream.addTrack(event.track);
        if (event.track.kind === 'video') resolve();
      });
    });
    await connection.setLocalDescription(await connection.createOffer());
    // Candidates travel inside the offer, so no trickle-ICE round trips are needed.
    await waitFor(iceGatheringComplete(connection), 2000, 'ICE').catch(() => {});
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/sdp' },
      body: connection.localDescription.sdp,
    });
    if (response.status !== 201) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `WebRTC: error ${response.status}`);
    }
    location = response.headers.get('Location');
    const answer = await response.text();
    session.codec = answer.match(/a=rtpmap:\d+ ([\w-]+)\/90000/)?.[1] || '';
    // MediaMTX marks media it will not send as inactive; only an active audio
    // section means the camera's microphone is actually reaching us.
    session.audio = /m=audio [1-9][\s\S]*?a=(sendonly|sendrecv)/.test(answer);
    await connection.setRemoteDescription({ type: 'answer', sdp: answer });
    // Ask for the smallest jitter buffer the browser allows: on a LAN there is
    // almost no network jitter to absorb, and the default adds ~100-200 ms.
    for (const receiver of connection.getReceivers()) {
      if ('jitterBufferTarget' in receiver) receiver.jitterBufferTarget = 0;
      if ('playoutDelayHint' in receiver) receiver.playoutDelayHint = 0;
    }
    session.stats = async () => {
      const report = await connection.getStats();
      for (const entry of report.values()) {
        if (entry.type !== 'inbound-rtp' || entry.kind !== 'video') continue;
        const previous = session.lastStats;
        session.lastStats = entry;
        // Delay added by the browser over the last interval, not since start.
        const emitted = entry.jitterBufferEmittedCount - (previous?.jitterBufferEmittedCount || 0);
        const delay = entry.jitterBufferDelay - (previous?.jitterBufferDelay || 0);
        return {
          bufferMs: emitted > 0 ? Math.round((delay / emitted) * 1000) : null,
          fps: entry.framesPerSecond ?? null,
        };
      }
      return null;
    };
    await waitFor(Promise.all([tracked, peerConnected(connection)]), 8000, 'WebRTC: el puerto 8189 no responde');
    connection.addEventListener('connectionstatechange', () => {
      if (connection.connectionState === 'failed') onLost('Conexión WebRTC perdida');
    });
    return session;
  } catch (error) {
    session.destroy();
    throw error;
  }
}

function startHLS(playlist, video, cameraId, onLost) {
  if (video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = playlist;
    return { mode: 'HLS', destroy() { video.removeAttribute('src'); video.load(); } };
  }
  if (!window.Hls?.isSupported()) throw new Error('Este navegador no admite la reproducción');
  const hls = new window.Hls({
    enableWorker: true,
    lowLatencyMode: true,
    // The camera sends a keyframe only every ~3 s; a tighter target than this
    // drains the buffer and causes the stall/catch-up cycle.
    liveSyncDuration: 1.6,
    liveMaxLatencyDuration: 5,
    maxLiveSyncPlaybackRate: 1.25,
    maxBufferLength: 6,
    maxMaxBufferLength: 10,
    backBufferLength: 0,
  });
  const attempts = { network: 0, media: 0 };
  hls.loadSource(playlist);
  hls.attachMedia(video);
  hls.on(window.Hls.Events.FRAG_BUFFERED, () => { attempts.network = 0; attempts.media = 0; });
  hls.on(window.Hls.Events.ERROR, (_event, data) => {
    reportEvent(cameraId, video, data.type || 'hls-error', data.details || '', Boolean(data.fatal), hls.latency);
    if (!data.fatal) return;
    if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR && attempts.network++ < 2) return hls.startLoad();
    if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR && attempts.media++ < 2) return hls.recoverMediaError();
    onLost('No se pudo reproducir el flujo');
  });
  return { mode: 'HLS', destroy() { hls.destroy(); video.removeAttribute('src'); video.load(); } };
}

// Returns a session with { mode, destroy() }. onLost(message) fires if it drops later.
export async function play(cameraId, video, onLost) {
  const urls = await api(`/api/cameras/${cameraId}/stream`, { method: 'POST' });
  if (urls.whep && window.RTCPeerConnection) {
    try {
      const session = await startWebRTC(urls.whep, video, onLost);
      reportEvent(cameraId, video, 'webrtc', session.codec);
      await video.play().catch(() => {});
      return session;
    } catch (error) {
      reportEvent(cameraId, video, 'webrtc-fallback', error.message);
    }
  }
  const session = startHLS(urls.playlist, video, cameraId, onLost);
  await video.play().catch(() => {});
  return session;
}
