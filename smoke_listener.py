import time
import numpy as np
import sounddevice as sd
import paho.mqtt.client as mqtt

# --- CONFIGURATION ---
MQTT_BROKER = "127.0.0.1" 
MQTT_PORT = 1883
MQTT_USER = None
MQTT_PASS = None
MQTT_TOPIC_STATE = "home/smoke_listener/state"

# Audio parameters
SAMPLE_RATE = 16000  # 16 kHz is plenty for frequency analysis
BLOCK_SIZE = 2048    # Number of frames per chunk (~0.128 sec)

# Acoustic filters (Standard T3 alarms usually sit between 3000Hz and 3600Hz)
TARGET_FREQ_MIN = 3000.0
TARGET_FREQ_MAX = 3700.0
VOLUME_THRESHOLD = 0.05  # Adjust based on mic sensitivity and distance
PEAK_ENERGY_THRESHOLD = 1.5  # Adjust based on testing

# State tracking for T3 pattern (3 beeps, pause)
pulse_timestamps = []

# Initialize MQTT Client
client = mqtt.Client()
if MQTT_USER and MQTT_PASS:
    client.username_pw_set(MQTT_USER, MQTT_PASS)

try:
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()
    print("[INFO] Connected to MQTT Broker.")
except Exception as e:
    print(f"[ERROR] Failed to connect to MQTT broker: {e}")
    exit(1)

def send_mqtt_alert(state_payload, message):
    payload = f'{{"state": "{state_payload}", "message": "{message}"}}'
    client.publish(MQTT_TOPIC_STATE, payload)
    print(f"[ALERT SENT] {payload}")

def audio_callback(indata, frames, time_info, status):
    global pulse_timestamps
    if status:
        return

    # Convert audio chunk to mono numpy array
    audio_data = indata[:, 0]
    
    # 1. Quick RMS Volume Check (skip heavy math if room is quiet)
    rms = np.sqrt(np.mean(audio_data**2))
    if rms < VOLUME_THRESHOLD:
        return

    # 2. Fast Fourier Transform (FFT) to check frequency peak
    fft_vals = np.fft.rfft(audio_data * np.hanning(len(audio_data)))
    fft_freqs = np.fft.rfftfreq(len(audio_data), 1 / SAMPLE_RATE)
    
    # Filter for the target frequency band
    band_mask = (fft_freqs >= TARGET_FREQ_MIN) & (fft_freqs <= TARGET_FREQ_MAX)
    if not np.any(band_mask):
        return

    peak_energy = np.max(np.abs(fft_vals[band_mask]))
    
    # If energy in the 3.1kHz - 3.5kHz range spikes sharply:
    if peak_energy > PEAK_ENERGY_THRESHOLD:  # Adjust threshold based on testing
        now = time.time()
        print(f"[DETECT] High-frequency tone detected at {now}")
        
        # Track timestamps to evaluate T3 cadence vs Low Battery Chirp
        pulse_timestamps.append(now)

        # Clean up timestamps older than 60 seconds (needed to catch slow chirps)
        pulse_timestamps = [t for t in pulse_timestamps if now - t < 60.0]

        # 1. Evaluate T3 Fire Alarm Pattern: 3 rapid pulses within ~1.5 seconds
        # Check if we have at least 3 recent pulses very close together
        recent_pulses = [t for t in pulse_timestamps if now - t < 2.0]
        if len(recent_pulses) >= 3:
            time_span = recent_pulses[-1] - recent_pulses[0]
            if 0.4 < time_span < 1.8:
                send_mqtt_alert("FIRE_ALARM", "T3 Smoke Alarm Pattern Detected!")
                pulse_timestamps.clear()  # Reset so we don't re-trigger immediately
        
        # 2. Evaluate Low Battery Chirp: Single isolated pulse
        # If we have a single pulse, and no other pulse happened in the last 2 seconds (ruling out T3)
        elif len(pulse_timestamps) == 1:
            # To avoid false positives from a single random noise spike, 
            # you can optionally wait or check if it matches a periodic cadence.
            # For a simple proof-of-concept, sending a low battery warning on an isolated tone:
            send_mqtt_alert("LOW_BATTERY", "Possible Low Battery Chirp Detected!")

# Start the audio input stream
print("[INFO] Listening for smoke alarm frequencies...")
try:
    with sd.InputStream(channels=1, samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE, callback=audio_callback):
        while True:
            time.sleep(1)
except KeyboardInterrupt:
    print("[INFO] Stopping listener.")
    client.loop_stop()
    client.disconnect()