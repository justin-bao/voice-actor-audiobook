import requests

with open('004_第一章_SSML.txt', 'r', encoding='utf-8') as f:
    ssml_content = f.read()

API_KEY = ""  # fill in
VOICE_ID = "DowyQ68vDpgFYdWVGjc3"  # Jason Chen narrator

# Split into 3-4 chunks (due to 10k char limit)
chunks = [ssml_content[i:i+9000] for i in range(0, len(ssml_content), 9000)]

for i, chunk in enumerate(chunks, 1):
    response = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}",
        headers={"xi-api-key": API_KEY, "Content-Type": "application/json"},
        json={"text": chunk, "model_id": "eleven_multilingual_v2"}
    )
    
    if response.status_code == 200:
        with open(f'chapter1_segment_{i}.mp3', 'wb') as f:
            f.write(response.content)
        print(f"✅ Segment {i} saved")
    else:
        print(f"❌ Error: {response.status_code}")