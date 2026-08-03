import numpy as np
from scipy.io import wavfile

# Audio settings
sample_rate = 44100  # Standard audio sampling rate
file_name = "call_waiting.wav"

# Frequency for Call Waiting Tone (Standard 440 Hz tone)
carrier_freq = 440  # Hz

# Timings (in seconds)
beep_duration = 0.15  # Duration of each beep (150 ms)
beep_gap = 0.15  # Short gap between the double-beeps (150 ms)
pause_duration = 3.0  # Silence duration before repeating double-beep (3 seconds)
total_cycles = 6  # Total repeat cycles


# Helper function to generate a smooth single beep
def generate_beep(duration):
    t = np.linspace(0, duration, int(sample_rate * duration), False)
    tone = np.sin(2 * np.pi * carrier_freq * t)

    # 10ms fade-in & fade-out to prevent popping sound
    fade_samples = int(sample_rate * 0.01)
    envelope = np.ones_like(tone)
    envelope[:fade_samples] = np.linspace(0, 1, fade_samples)
    envelope[-fade_samples:] = np.linspace(1, 0, fade_samples)

    return tone * envelope


# Generate the components
beep = generate_beep(beep_duration)
short_pause = np.zeros(int(sample_rate * beep_gap))
long_pause = np.zeros(int(sample_rate * pause_duration))

# Combine into a double-beep cycle: [Beep] + [Short Pause] + [Beep] + [Long Pause]
one_cycle = np.concatenate((beep, short_pause, beep, long_pause))

# Repeat cycles
full_audio = np.tile(one_cycle, total_cycles)

# Normalize to 16-bit PCM format
full_audio = np.int16(full_audio / np.max(np.abs(full_audio)) * 32767)

# Save .wav audio file
wavfile.write(file_name, sample_rate, full_audio)

print(f"Call Waiting Tone ready! Saved as '{file_name}'")