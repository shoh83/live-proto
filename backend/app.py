# app.py
# -*- coding: utf-8 -*-

import asyncio
import traceback
import wave
import websockets
import pyaudio
from google import genai
from google.genai import types

# Audio & file constants
FORMAT    = pyaudio.paInt16
CHANNELS  = 1
SEND_SR   = 16000
RECV_SR   = 24000

INPUT_WAV    = 'user_input.wav'
RESPONSE_WAV = 'response.wav'

# Initialize PyAudio and GenAI client
audio_engine = pyaudio.PyAudio()
client       = genai.Client()  # Make sure GOOGLE_API_KEY is set

PROMPT = """
You are an approachable, patient English tutor specializing in beginner Korean speakers. Today’s lesson is a simulated restaurant scenario where the learner practices ordering food in English.

1. Set the scene: Describe the restaurant (e.g., “You’ve just been seated at a cozy café with international cuisine.”).
2. Model key phrases: Introduce essential expressions (e.g., greetings, asking for the menu, ordering, requesting the bill).
3. Elicit learner speech: Prompt the student to respond using those phrases, one at a time.
4. Expand and vary: After each learner attempt, offer synonyms, polite alternatives, or follow-up questions (e.g., “Great—‘I’d like…’ works well. You could also say ‘Could I have…?’ How would you ask if the dish is spicy?”).
5. Correct and reinforce: Gently correct pronunciation or grammar, then have the learner repeat.
6. Wrap up: Summarize what was covered and suggest a short role-play (e.g., “Let’s switch roles: you’re the waiter—what do you ask?”).

Goal: Guide the learner step-by-step through realistic exchanges, encourage them to try new phrases, and build confidence ordering food in an English-speaking restaurant."""

# Gemini LiveConnect configuration
MODEL  = 'gemini-2.5-flash-preview-native-audio-dialog'
CONFIG = types.LiveConnectConfig(
    response_modalities=['AUDIO'],
    media_resolution='MEDIA_RESOLUTION_LOW',
    speech_config=types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name='Zephyr')
        )
    ),
    context_window_compression=types.ContextWindowCompressionConfig(
        trigger_tokens=25600,
        sliding_window=types.SlidingWindow(target_tokens=12800),
    ),
    system_instruction=types.Content(
        parts=[types.Part.from_text(
            text=PROMPT
        )],
        role='user'
    ),
)

class AudioLoop:
    def __init__(self, ws):
        self.ws          = ws
        self.session     = None
        self.audio_out_q = asyncio.Queue(maxsize=5)

        # — INPUT WAV (what we send *to* Gemini) —
        self.wav_in = wave.open(INPUT_WAV, 'wb')
        self.wav_in.setnchannels(CHANNELS)
        self.wav_in.setsampwidth(audio_engine.get_sample_size(FORMAT))
        self.wav_in.setframerate(SEND_SR)

        # — RESPONSE WAV (what we get *from* Gemini) —
        self.wav_out = wave.open(RESPONSE_WAV, 'wb')
        self.wav_out.setnchannels(CHANNELS)
        self.wav_out.setsampwidth(audio_engine.get_sample_size(FORMAT))
        self.wav_out.setframerate(RECV_SR)

    async def ws_reader(self):
        """
        Read raw PCM from the browser, record it, and enqueue for Gemini.
        """
        async for chunk in self.ws:
            # chunk is bytes of Int16 PCM @16kHz
            self.wav_in.writeframes(chunk)
            await self.audio_out_q.put({'data': chunk, 'mime_type': 'audio/pcm'})

    async def send_realtime(self):
        """
        Pull queued mic chunks and send them into Gemini LiveConnect.
        """
        while True:
            pkt = await self.audio_out_q.get()
            await self.session.send_realtime_input(audio=pkt)

    async def receive_and_forward(self):
        """
        Read Gemini’s audio replies, record them to response.wav
        and forward the raw PCM back to the browser.
        """
        try:
            while True:
                turn = self.session.receive()
                async for response in turn:
                    if response.data:
                        # record to response.wav
                        self.wav_out.writeframes(response.data)
                        # forward to client
                        await self.ws.send(response.data)
        finally:
            # WAV files are closed in the handler’s cleanup
            pass

    def close(self):
        """Close both WAV files once all tasks are done."""
        try:
            self.wav_in.close()
        except Exception:
            pass
        try:
            self.wav_out.close()
        except Exception:
            pass

async def handler(ws):
    loop = AudioLoop(ws)
    try:
        # 1) Open a LiveConnect session and launch all tasks
        async with client.aio.live.connect(model=MODEL, config=CONFIG) as session:
            loop.session = session

            # Run all three loops in parallel; cancel all if any exits/errors
            async with asyncio.TaskGroup() as tg:
                tg.create_task(loop.ws_reader())
                tg.create_task(loop.send_realtime())
                tg.create_task(loop.receive_and_forward())

    except Exception:
        traceback.print_exc()
    finally:
        # Only close files once *all* tasks have finished
        loop.close()
        await ws.close()

async def main():
    server = await websockets.serve(handler, '0.0.0.0', 8765)
    print('WebSocket + Gemini server running on ws://0.0.0.0:8765')
    await server.wait_closed()

if __name__ == '__main__':
    asyncio.run(main())
