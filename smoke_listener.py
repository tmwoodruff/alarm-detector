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
VOLUME_THRESHOLD = 0.7  # Adjust based on mic sensitivity and distance
REQUIRED_CHUNKS = 2     # Must sustain for ~0.25 seconds to count as a valid pulse
TONALITY_THRESHOLD = 15.0  # Adjust this ratio based on testing (pure tones yield very high ratios, speech yields low ratios)



# Track startup time to ignore driver initialization artifacts
WARMUP_PERIOD = 3.0  # Ignore audio for the first 3 seconds
STARTUP_TIME = time.time()

# State tracking for T3 pattern (3 beeps, pause)
pulse_timestamps = []
consecutive_alarm_chunks = 0

# Initialize MQTT Client
client = mqtt.Client()
if MQTT_USER and MQTT_PASS:
    client.username_pw_set(MQTT_USER, MQTT_PASS)

print("[INFO] Connecting to MQTT Broker...")
while True:
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
        print("[INFO] Connected to MQTT Broker successfully.")
        break
    except Exception as e:
        print(f"[WARN] Broker not ready yet ({e}). Retrying in 5 seconds...")
        time.sleep(5)

def send_mqtt_alert(state_payload, message):
    payload = f'{{"state": "{state_payload}", "message": "{message}"}}'
    client.publish(MQTT_TOPIC_STATE, payload)
    print(f"[ALERT SENT] {payload}")

def audio_callback(indata, frames, time_info, status):
    global pulse_timestamps, consecutive_alarm_chunks
    if status:
        return

    # Ignore audio during the initial hardware warm-up window
    if time.time() - STARTUP_TIME < WARMUP_PERIOD:
        return

    # Evaluate Low Battery Chirp (Single isolated pulse with no noise 5 sec prior and 2 sec after)
    if len(pulse_timestamps) > 0:
        now = time.time()
        low_battery_pulses = [t for t in pulse_timestamps if now - t <= 5.0 and now - t > 2.0]
        if len(low_battery_pulses) == 1:
            send_mqtt_alert("LOW_BATTERY", "Possible Low Battery Chirp Detected!")
            pulse_timestamps.clear()

    # Convert audio chunk to mono numpy array
    audio_data = indata[:, 0]
    
    # Quick RMS Volume Check (skip heavy math if room is quiet)
    rms = np.sqrt(np.mean(audio_data**2))
    if rms < VOLUME_THRESHOLD:
        return

    # FFT to check frequency peak
    fft_vals = np.fft.rfft(audio_data * np.hanning(len(audio_data)))
    fft_freqs = np.fft.rfftfreq(len(audio_data), 1 / SAMPLE_RATE)
    
    # Filter for the target frequency band
    band_mask = (fft_freqs >= TARGET_FREQ_MIN) & (fft_freqs <= TARGET_FREQ_MAX)
    if not np.any(band_mask):
        return

    magnitude = np.abs(fft_vals)
    
    # Tonality Check (Spectral Peak vs. Average Energy)
    # Smoke alarms are pure tones. We check if the peak in our target band 
    # is significantly higher than the average noise floor across the whole spectrum.
    band_mask = (fft_freqs >= TARGET_FREQ_MIN) & (fft_freqs <= TARGET_FREQ_MAX)
    if not np.any(band_mask):
        consecutive_alarm_chunks = 0
        return

    target_peak = np.max(magnitude[band_mask])
    average_noise = np.mean(magnitude) if np.mean(magnitude) > 0 else 1e-6
    
    # Ratio of peak frequency to general background noise
    tonality_ratio = target_peak / average_noise

    if tonality_ratio > TONALITY_THRESHOLD:
        consecutive_alarm_chunks += 1
    else:
        consecutive_alarm_chunks = 0
        return

    # Sustained Duration Check (Filters out quick snaps, claps, and pops)
    if consecutive_alarm_chunks <= REQUIRED_CHUNKS:
        return
    
    now = time.time()
    print(f"[DETECT] Valid smoke alarm pulse confirmed at {now}")
    
    # Reset counter so we wait for the *next* beep burst
    consecutive_alarm_chunks = 0 
    
    pulse_timestamps.append(now)
    pulse_timestamps = [t for t in pulse_timestamps if now - t < 10.0]

    # Evaluate T3 Fire Alarm Pattern (3 rapid pulses within 2 (and a bit) seconds)
    t3_window_pulses = [t for t in pulse_timestamps if now - t <= 2.5]
    if len(t3_window_pulses) >= 3:
        time_span = t3_window_pulses[-1] - t3_window_pulses[0]
        if 0.4 < time_span < 2.0:
            send_mqtt_alert("FIRE_ALARM", "T3 Smoke Alarm Pattern Detected!")
            pulse_timestamps.clear()
            return


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
