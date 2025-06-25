# -*- coding: utf-8 -*-
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
## Setup

To install the dependencies for this script, run:

```
brew install portaudio
pip install -U google-genai pyaudio
```

## API key

Ensure the `GOOGLE_API_KEY` environment variable is set to the api-key
you obtained from Google AI Studio.

## Run

To run the script:

```
python Get_started_LiveAPI_NativeAudio.py
```

Start talking to Gemini
"""

import asyncio
import sys
import traceback
import time
from google.genai import types
import pyaudio
import wave
from google import genai

if sys.version_info < (3, 11, 0):
    import taskgroup, exceptiongroup

    asyncio.TaskGroup = taskgroup.TaskGroup
    asyncio.ExceptionGroup = exceptiongroup.ExceptionGroup

FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 4096

pya = pyaudio.PyAudio()


client = genai.Client()  # GOOGLE_API_KEY must be set as env variable

MODEL = "gemini-live-2.5-flash-preview"
CONFIG = types.LiveConnectConfig(
    response_modalities=[
        "AUDIO",
    ],
    media_resolution="MEDIA_RESOLUTION_LOW",
    speech_config=types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Zephyr")
        )
    ),
    context_window_compression=types.ContextWindowCompressionConfig(
        trigger_tokens=25600,
        sliding_window=types.SlidingWindow(target_tokens=12800),
    ),
    system_instruction=types.Content(
        parts=[types.Part.from_text(text="You are a friendly English teacher. Practice ordering food at a restaurant with a Korean learner.")],
        role="user"
    ),
)


class AudioLoop:
    def __init__(self):
        self.audio_in_queue = None
        self.out_queue = None

        self.session = None

        self.audio_stream = None

        self.receive_audio_task = None
        self.play_audio_task = None
        # Prepare for WAV output:
        self.wav_out = wave.open("gemini_output.wav", "wb")
        self.wav_out.setnchannels(CHANNELS)
        # sample width in bytes (e.g. 2 for paInt16)
        self.wav_out.setsampwidth(pya.get_sample_size(FORMAT))
        self.wav_out.setframerate(RECEIVE_SAMPLE_RATE)

         # — INPUT WAV (what you send *to* Gemini) —
        self.wav_in = wave.open("gemini_input.wav", "wb")
        self.wav_in.setnchannels(CHANNELS)
        self.wav_in.setsampwidth(pya.get_sample_size(FORMAT))
        self.wav_in.setframerate(SEND_SAMPLE_RATE)

    async def listen_audio(self):
        mic_info = pya.get_default_input_device_info()
        self.audio_stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=SEND_SAMPLE_RATE,
            input=True,
            input_device_index=mic_info["index"],
            frames_per_buffer=CHUNK_SIZE,
        )
        if __debug__:
            kwargs = {"exception_on_overflow": False}
        else:
            kwargs = {}
        while True:
            data = await asyncio.to_thread(self.audio_stream.read, CHUNK_SIZE, **kwargs)
            # write each mic-chunk out to your input WAV
            self.wav_in.writeframes(data)
            # then send it on to Gemini
            await self.out_queue.put({"data": data, "mime_type": "audio/pcm"})

    async def send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(audio=msg)

    async def receive_audio(self):
        "Background task to reads from the websocket and write pcm chunks to the output queue"
        try:
            while True:
                turn = self.session.receive()
                async for response in turn:
                    if data := response.data:
                        # write raw PCM bytes to WAV file
                        self.wav_out.writeframes(data)
                        # enqueue for latency measurement/playback
                        self.audio_in_queue.put_nowait((data, time.time()))
                        continue
                    if text := response.text:
                        print(text, end="")

                # … your interruption logic …
                # If you interrupt the model, it sends a turn_complete.
                # For interruptions to work, we need to stop playback.
                # So empty out the audio queue because it may have loaded
                # much more audio than has played yet.
                # while not self.audio_in_queue.empty():
                #     self.audio_in_queue.get_nowait()
        finally:
            # Make sure we close the WAV file when done
            self.wav_out.close()

            

    async def play_audio(self):
        # open output stream
        stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=RECEIVE_SAMPLE_RATE,
            output=True,
        )

        # pre-buffer INITIAL_CHUNKS before playback
        INITIAL_CHUNKS = int((RECEIVE_SAMPLE_RATE * 0.1) / CHUNK_SIZE)  # ~100 ms
        startup_buffer = []
        for _ in range(INITIAL_CHUNKS):
            item = await self.audio_in_queue.get()
            # unpack tuple or raw
            chunk = item[0] if isinstance(item, tuple) else item
            startup_buffer.append(chunk)

        # play buffered chunks
        for chunk in startup_buffer:
            await asyncio.to_thread(stream.write, chunk)

        # continuous playback
        while True:
            item = await self.audio_in_queue.get()
            chunk = item[0] if isinstance(item, tuple) else item
            # optional latency log
            if isinstance(item, tuple):
                ts = item[1]
                latency_ms = (time.time() - ts) * 1000
                print(f"[Latency] {latency_ms:.1f} ms")
            await asyncio.to_thread(stream.write, chunk)

    async def run(self):
        try:
            async with (
                client.aio.live.connect(model=MODEL, config=CONFIG) as session,
                asyncio.TaskGroup() as tg,
            ):
                self.session = session

                self.audio_in_queue = asyncio.Queue()
                self.out_queue = asyncio.Queue(maxsize=5)

                tg.create_task(self.send_realtime())
                tg.create_task(self.listen_audio())
                tg.create_task(self.receive_audio())
                tg.create_task(self.play_audio())
        except asyncio.CancelledError:
            pass
        except ExceptionGroup as EG:
            if self.audio_stream:
                self.audio_stream.close()
            traceback.print_exception(EG)


if __name__ == "__main__":
    loop = AudioLoop()
    asyncio.run(loop.run())