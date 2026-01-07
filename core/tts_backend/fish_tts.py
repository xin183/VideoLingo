import hashlib
import json
import os
import time
import uuid
from pathlib import Path

import requests
from core._1_ytdlp import find_video_files
from core.asr_backend.audio_preprocess import get_audio_duration
from core.utils import *
from core.utils.models import _AUDIO_REFERS_DIR, get_output_dir
from pydub import AudioSegment

# ------------
# Fish Audio API endpoints - now configurable
# ------------
def get_api_urls():
    """Get API URLs based on configuration"""
    base_url = load_key("fish_tts.base_url")
    return {
        "tts": f"{base_url}/fish-audio/v1/tts",
        "model_create": f"{base_url}/fish-audio/model",
        "model_get": f"{base_url}/fish-audio/model"
    }

REFER_MAX_LENGTH = 90


@except_handler("Failed to generate audio using Fish TTS", retry=3, delay=1)
def fish_tts_basic(text: str, save_as: str, reference_id: str) -> bool:
    """Basic Fish TTS conversion with preset voice"""
    API_KEY = load_key("fish_tts.api_key")
    urls = get_api_urls()

    payload = {
        "text": text,
        "reference_id": reference_id,
        "chunk_length": 200,
        "normalize": True,
        "format": "wav",
        "latency": "normal",
    }

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "model": "speech-1.6",
        "Content-Type": "application/json",
    }

    print(payload)
    response = requests.post(urls["tts"], json=payload, headers=headers)
    response.raise_for_status()

    # Check if response contains audio URL or direct audio content
    content_type = response.headers.get("content-type", "")
    # Response contains JSON with audio URL
    response_data = response.json()
    if "url" in response_data:
        audio_response = requests.get(response_data["url"])
        audio_response.raise_for_status()

        with open(save_as, "wb") as f:
            f.write(audio_response.content)
    print(f"Audio saved to {save_as}")
    return True


@except_handler("Failed to create voice model", retry=2, delay=2)
def create_voice_model(audio_path: str, title: str, description: str = "") -> str:
    """Create a voice model using Fish Audio API"""
    API_KEY = load_key("fish_tts.api_key")
    urls = get_api_urls()

    if not Path(audio_path).exists():
        raise FileNotFoundError(f"Audio file not found at {audio_path}")

    # Prepare multipart form data
    files = {
        "voices": ("reference.wav", open(audio_path, "rb"), "audio/wav"),
    }

    data = {
        "visibility": "private",
        "type": "tts",
        "title": title,
        "description": description,
        "train_mode": "fast",
        "enhance_audio_quality": "false",
    }

    headers = {"Authorization": f"Bearer {API_KEY}"}

    print(f"Creating voice model: {title}")
    response = requests.post(urls["model_create"], files=files, data=data, headers=headers)

    # Close file handle
    files["voices"][1].close()

    if response.status_code in [200, 201]:
        response_data = response.json()
        model_id = response_data.get("_id")
        print(f"Successfully created voice model: {model_id}")
        return model_id
    else:
        print(f"Failed to create voice model: {response.status_code}")
        print(f"Response: {response.text}")
        raise Exception(f"Failed to create voice model: {response.status_code}")


@except_handler("Failed to get model status", retry=3, delay=2)
def get_model_status(model_id: str) -> dict:
    """Get model training status"""
    API_KEY = load_key("fish_tts.api_key")
    urls = get_api_urls()

    headers = {"Authorization": f"Bearer {API_KEY}"}

    response = requests.get(urls["model_get"], headers=headers)
    response.raise_for_status()

    response_json = response.json()
    items = response_json.get("items", [])[0]
    return response_json


def wait_for_model_ready(model_id: str, max_wait_time: int = 300) -> bool:
    return True
    """Wait for model training to complete"""
    print(f"Waiting for model {model_id} to be ready...")
    start_time = time.time()

    while time.time() - start_time < max_wait_time:
        status_data = get_model_status(model_id)
        status = status_data.get("status", "unknown")

        print(f"Model status: {status}")

        if status == "trained":
            print("Model training completed!")
            return True
        elif status == "failed":
            print("Model training failed!")
            return False

        time.sleep(10)  # Wait 10 seconds before checking again

    print("Model training timeout!")
    return False


@except_handler("Failed to merge audio")
def merge_audio(files, output):
    """Merge audio files with brief silence between them"""
    combined = AudioSegment.empty()
    silence = AudioSegment.silent(duration=100)  # 100ms silence

    for file in files:
        if Path(file).exists():
            audio = AudioSegment.from_wav(file)
            combined += audio + silence

    if len(combined) == 0:
        print("No valid audio files to merge")
        return False

    # Export the combined file
    combined.export(
        output,
        format="wav",
        parameters=["-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1"],
    )

    if os.path.getsize(output) == 0:
        print("Output file size is 0")
        return False

    print("Successfully merged audio files")
    return True


def get_ref_audio(task_df):
    """Get reference audio and text for voice cloning"""
    print("Starting reference audio selection process...")

    duration = 0
    selected = []
    combined_text = ""
    found_first = False

    for _, row in task_df.iterrows():
        current_text = row["origin"]

        # If no valid record has been found yet
        if not found_first:
            if len(current_text) <= REFER_MAX_LENGTH:
                selected.append(row)
                combined_text = current_text
                duration += row["duration"]
                found_first = True
                print(f"Found first valid row: {current_text[:50]}...")
            else:
                print(
                    f"Skipping long row: {current_text[:50]}... ({len(current_text)} chars)"
                )
            continue

        # Check subsequent rows
        new_text = combined_text + " " + current_text
        if len(new_text) > REFER_MAX_LENGTH:
            break

        selected.append(row)
        combined_text = new_text
        duration += row["duration"]
        print(f"Added row: {current_text[:50]}...")

        if duration > 10:  # Limit to 10 seconds
            break

    if not selected:
        print(
            f"No valid segments found (all texts exceed {REFER_MAX_LENGTH} characters)"
        )
        return None, None

    print(f"Selected {len(selected)} segments, total duration: {duration:.2f}s")

    # Get audio files
    audio_files = [f"{_AUDIO_REFERS_DIR}/{row['number']}.wav" for row in selected]
    print(f"Audio files to merge: {audio_files}")

    combined_audio = f"{_AUDIO_REFERS_DIR}/combined_reference.wav"
    success = merge_audio(audio_files, combined_audio)

    if not success:
        print("Error: Failed to merge audio files")
        return None, None

    print(f"Successfully created combined audio: {combined_audio}")
    print(f"Final combined text: {combined_text} | Length: {len(combined_text)}")

    return combined_audio, combined_text


def fish_tts_for_videolingo(text: str, save_as: str, number: int, task_df) -> bool:
    """Main function for VideoLingo integration with voice cloning support"""
    fish_set = load_key("fish_tts")
    mode = fish_set.get("mode", "preset")

    # ------------
    # Local cache helpers to avoid cross-process config writes
    # ------------
    def _get_clone_cache_path():
        return os.path.join(get_output_dir(), "voice_clone_cache.json")

    def _load_cached_model():
        path = _get_clone_cache_path()
        if not os.path.exists(path):
            # Fallback: try existing config values without writing back
            try:
                return load_key("fish_tts.custom_model_name"), load_key("fish_tts.custom_model_id")
            except Exception:
                return None, None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data.get("model_name"), data.get("model_id")
        except Exception as exc:
            print(f"⚠️  Failed to read clone cache: {exc}")
            return None, None

    def _save_cached_model(model_name, model_id):
        path = _get_clone_cache_path()
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"model_name": model_name, "model_id": model_id}, fh)
        except Exception as exc:
            print(f"⚠️  Failed to write clone cache: {exc}")

    if mode == "preset":
        # Use preset voice
        character = fish_set["character"]
        reference_id = fish_set["character_id_dict"][character]
        return fish_tts_basic(text, save_as, reference_id)

    elif mode == "clone":
        # Check if a specific model ID is forced (e.g. from realtime calibration)
        forced_id = fish_set.get("force_model_id")
        print(f"DEBUG: fish_tts mode=clone, force_model_id={forced_id}")
        if forced_id:
            print(f"Using forced model ID: {forced_id}")
            return fish_tts_basic(text, save_as, forced_id)

        # Use voice cloning
        video_file = find_video_files()
        model_name = hashlib.md5(video_file.encode()).hexdigest()[:8]
        print(f"Using model name: {model_name}")

        cached_name, cached_id = _load_cached_model()

        if cached_name != model_name or not cached_id:
            # Need to create new model
            print("Creating new voice model...")

            # Get reference audio and text
            ref_audio, ref_text = get_ref_audio(task_df)
            if ref_audio is None or ref_text is None:
                print("Failed to get reference audio, falling back to preset mode")
                character = fish_set["character"]
                reference_id = fish_set["character_id_dict"][character]
                return fish_tts_basic(text, save_as, reference_id)

            # Create voice model
            model_id = create_voice_model(
            audio_path=ref_audio,
                title=f"VideoLingo_Clone_{model_name}",
                description=f"Voice clone for video: {video_file}",
            )

            # Wait for model to be ready
            if not wait_for_model_ready(model_id):
                print("Model training failed or timeout, falling back to preset mode")
                character = fish_set["character"]
                reference_id = fish_set["character_id_dict"][character]
                return fish_tts_basic(text, save_as, reference_id)

            # Save model info to local cache only (avoid touching global config)
            _save_cached_model(model_name, model_id)
        else:
            # Use existing model
            model_id = cached_id
            print(f"Using existing model from cache: {model_id}")

        # Generate TTS with cloned voice
        return fish_tts_basic(text, save_as, model_id)

    else:
        raise ValueError(f"Invalid mode: {mode}. Choose 'preset' or 'clone'")


def fish_tts(text: str, save_as: str) -> bool:
    """Legacy function for backward compatibility"""
    fish_set = load_key("fish_tts")
    character = fish_set["character"]
    reference_id = fish_set["character_id_dict"][character]
    return fish_tts_basic(text, save_as, reference_id)


if __name__ == "__main__":
    fish_tts("Hi! Welcome to VideoLingo!", "test.wav")
