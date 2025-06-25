// app.js

const WS_URL = 'ws://localhost:8765';
const constraints = {
  audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
};

let socket;
let recContext;    // capture context @16kHz
let playContext;   // playback context @24kHz
let nextStartTime;
let mediaStream;   // the getUserMedia stream
let processor;     // ScriptProcessorNode

// Wait until WebSocket is open
function socketReady(ws) {
  return new Promise(res => {
    if (ws.readyState === WebSocket.OPEN) res();
    else ws.addEventListener('open', () => res());
  });
}

// Schedule a PCM chunk via Web Audio
function handlePlaybackChunk(arrayBuffer) {
  const pcm16 = new Int16Array(arrayBuffer);
  const float32 = new Float32Array(pcm16.length);
  for (let i = 0; i < pcm16.length; i++) {
    float32[i] = pcm16[i] / 0x7FFF;
  }
  const buffer = playContext.createBuffer(1, float32.length, 24000);
  buffer.copyToChannel(float32, 0);

  const src = playContext.createBufferSource();
  src.buffer = buffer;
  src.connect(playContext.destination);

  // Clamp so we never schedule in the past
  const now = playContext.currentTime;
  const minStart = now + 0.05;
  if (nextStartTime < minStart) nextStartTime = minStart;

  src.start(nextStartTime);
  nextStartTime += buffer.duration;
}

// Start streaming
async function startStreaming() {
  // 1) Open WS
  socket = new WebSocket(WS_URL);
  socket.binaryType = 'arraybuffer';
  await socketReady(socket);

  // 2) Playback context
  playContext = new AudioContext({ sampleRate: 24000 });
  if (playContext.state === 'suspended') await playContext.resume();
  nextStartTime = playContext.currentTime + 0.1;
  socket.addEventListener('message', ev => handlePlaybackChunk(ev.data));

  // 3) Capture context
  mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
  recContext = new AudioContext({ sampleRate: 16000 });
  const source = recContext.createMediaStreamSource(mediaStream);

  // 4) Processor node (ScriptProcessor)
  processor = recContext.createScriptProcessor(4096, 1, 1);
  source.connect(processor);
  processor.connect(recContext.destination); // needed to fire onaudioprocess

  processor.onaudioprocess = e => {
    const float32 = e.inputBuffer.getChannelData(0);
    const int16 = new Int16Array(float32.length);
    for (let i = 0; i < float32.length; i++) {
      int16[i] = Math.max(-1, Math.min(1, float32[i])) * 0x7FFF;
    }
    if (socket.readyState === WebSocket.OPEN) {
      socket.send(int16.buffer);
    }
  };

  // Enable/disable buttons
  document.getElementById('start').disabled = true;
  document.getElementById('stop').disabled  = false;
}

// Stop streaming
function stopStreaming() {
  // 1) Stop sending audio
  if (processor) {
    processor.disconnect();
    processor.onaudioprocess = null;
    processor = null;
  }
  // 2) Stop tracks
  if (mediaStream) {
    mediaStream.getTracks().forEach(t => t.stop());
    mediaStream = null;
  }
  // 3) Close capture context
  if (recContext) {
    recContext.close();
    recContext = null;
  }
  // 4) Close WebSocket
  if (socket) {
    socket.close();
    socket = null;
  }

  // Enable/disable buttons
  document.getElementById('start').disabled = false;
  document.getElementById('stop').disabled  = true;
}

// Wire up buttons
document.getElementById('start').addEventListener('click', startStreaming);
document.getElementById('stop').addEventListener('click',  stopStreaming);
