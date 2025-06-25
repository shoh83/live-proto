import asyncio
import websockets
import wave

# WAV setup
WAV_FILE = 'output.wav'
CHANNELS = 1
SAMPLE_WIDTH = 2     # bytes (16-bit)
SAMPLE_RATE = 16000  # Hz

async def handler(websocket):  # only websocket, no path
    # Open WAV file once connection is established
    wav = wave.open(WAV_FILE, 'wb')
    wav.setnchannels(CHANNELS)
    wav.setsampwidth(SAMPLE_WIDTH)
    wav.setframerate(SAMPLE_RATE)
    print(f"Recording to {WAV_FILE}...")
    try:
        async for message in websocket:
            # message is raw PCM bytes
            wav.writeframes(message)
    finally:
        wav.close()
        print("Recording stopped.")

async def main():
    server = await websockets.serve(handler, '0.0.0.0', 8765)
    print("WebSocket server running on ws://0.0.0.0:8765")
    await server.wait_closed()

if __name__ == '__main__':
    asyncio.run(main())