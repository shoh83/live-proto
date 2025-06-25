// Ensure your server is listening on ws://localhost:8765
const WS_URL = 'ws://localhost:8765';
const constraints = { audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } };
let socket;
let audioContext;
let processor;

document.getElementById('start').addEventListener('click', async () => {
  socket = new WebSocket(WS_URL);
  await socketReady(socket);

  // Get mic with AEC
  const stream = await navigator.mediaDevices.getUserMedia(constraints);
  audioContext = new AudioContext({ sampleRate: 16000 });
  const source = audioContext.createMediaStreamSource(stream);

  // ScriptProcessor for PCM capture
  processor = audioContext.createScriptProcessor(4096, 1, 1);
  source.connect(processor);
  processor.connect(audioContext.destination);

  processor.onaudioprocess = (e) => {
    const float32 = e.inputBuffer.getChannelData(0);
    const int16 = new Int16Array(float32.length);
    for (let i = 0; i < float32.length; i++) {
      int16[i] = Math.max(-1, Math.min(1, float32[i])) * 0x7FFF;
    }
    if (socket.readyState === WebSocket.OPEN) {
      socket.send(int16.buffer);
    }
  };
});

// Helper to await socket open
function socketReady(ws) {
  return new Promise((resolve) => {
    if (ws.readyState === WebSocket.OPEN) return resolve();
    ws.addEventListener('open', () => resolve());
  });
}