# app.py
# -*- coding: utf-8 -*-
import json
import asyncio
import traceback
import wave
import websockets
import pyaudio
from google import genai
from google.genai import types
from websockets.exceptions import ConnectionClosedError

# Audio & file constants
FORMAT    = pyaudio.paInt16
CHANNELS  = 1
SEND_SR   = 16000
RECV_SR   = 24000

INPUT_WAV    = '/Users/seung/Desktop/user_input.wav'
RESPONSE_WAV = '/Users/seung/Desktop/response.wav'

# Initialize PyAudio and GenAI client
audio_engine = pyaudio.PyAudio()
client       = genai.Client()  # Make sure GOOGLE_API_KEY is set

PROMPT = """You are an approachable, patient English tutor specializing in beginner Korean speakers. Today’s lesson is a simulated airport check-in at John F. Kennedy International Airport (JFK) where you play the role of Delta Air Lines staff helping a passenger check in for their flight. Make sure you speak very slowly for a beginner to understand what you say. Give a one- to two-second pause between sentences.

1. Set the scene
– Describe the setting: “You’ve arrived at the departure hall of JFK in Queens, New York. You’re standing at the Delta Air Lines check-in counter in Terminal 2.”

2. Model key phrases
– Introduce essential expressions in context:
 - Staff: “Good morning! Welcome to Delta Air Lines. May I see your passport and confirmation code?”
 - Staff: “How many bags will you be checking today?”
 - Staff: “Would you like an aisle seat or a window seat with an ocean view?”
 - Staff: “Here’s your boarding pass. Your gate is C3, and boarding begins at 3:45 PM.”

3. Elicit learner speech
– Prompt the student to respond step by step:
 - Tutor: “What would you say in English if you want to change your seat?”
 - Tutor: “Now ask if there’s a fee for extra baggage.”

4. Expand and vary
– After each learner attempt, offer synonyms and alternatives:

5. Correct and reinforce
– Gently correct any pronunciation or grammar issues, then have the learner repeat the improved phrase.

6. Role-play reversal
– Swap roles: the student becomes the staff and asks you, the passenger, for information (e.g., “How many bags will you be checking?”).

7. Wrap up and review
– Summarize the key phrases learned and encourage a final mini role-play combining all steps: checking in, choosing a seat, and confirming the boarding details.

Goal: Provide step-by-step guidance through realistic interactions at a Delta Air Lines check-in counter at JFK, building confidence with authentic travel-related English expressions."""

# Gemini LiveConnect configuration
MODEL  = 'gemini-2.5-flash-preview-native-audio-dialog'
# MODEL  = 'gemini-live-2.5-flash-preview'
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
        async for msg in self.ws:
            # 1) JSON control frames
            if isinstance(msg, str):
                try:
                    ctrl = json.loads(msg)
                except json.JSONDecodeError:
                    continue

                if ctrl.get('cmd') == 'text' and 'text' in ctrl:
                    # send a text turn immediately, marking it as complete
                    await self.session.send_client_content(
                        turns={'role': 'user',
                               'parts': [{'text': ctrl['text']}]},
                        turn_complete=True
                    )
                    continue

            # 2) binary = raw PCM chunk
            #    record it and enqueue for streaming to Gemini
            # chunk is bytes of Int16 PCM @16kHz
            self.wav_in.writeframes(msg)
            await self.audio_out_q.put({'data': msg, 'mime_type': 'audio/pcm'})

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
                    # Detect server‐side VAD interruption
                    if getattr(response, "server_content", None):
                        if response.server_content.interrupted:
                            # Notify client to flush playback
                            await self.ws.send(json.dumps({"interrupted": True}))
                            # skip any further data in this turn
                            break

                    if response.data:
                        # record to response.wav
                        self.wav_out.writeframes(response.data)
                        try:
                        # forward to client
                            await self.ws.send(response.data)
                        except ConnectionClosedOK:
                            # Client closed the WebSocket (1005); stop sending
                            return
        except ConnectionClosedError as e:
            # transient internal error from Gemini Live API; swallow and let handler clean up
            print(f"[Warning] LiveConnect connection closed: {e}")
        except Exception as e:
            # any other unexpected exception
            traceback.print_exception(e)
        # finally:
        #     # WAV files are closed in the handler’s cleanup
        #     pass

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
