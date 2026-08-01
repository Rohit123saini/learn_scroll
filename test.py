import numpy as np
from scipy.io import wavfile

# Audio settings
sample_rate = 44100  # Standard audio sampling rate
file_name = "incoming_ring.mp3"

# Frequencies for Classic Landline Ring
carrier_freq1 = 400  # Hz
carrier_freq2 = 450  # Hz
ring_burst_freq = 20 # Hz (Trrr... trrr... vibrate effect)

# Timings (in seconds)
ring_duration = 1.0   # Sound duration (1 second)
pause_duration = 2.0  # Silence duration (2 seconds)
total_cycles = 5     # Ring repeat cycles

# Generate 1 second modulated ring sound
t_ring = np.linspace(0, ring_duration, int(sample_rate * ring_duration), False)

# Dual tone combination
base_tone = 0.5 * (np.sin(2 * np.pi * carrier_freq1 * t_ring) + np.sin(2 * np.pi * carrier_freq2 * t_ring))

# Amplitude Modulation (creates the rapid 'trrr' pulsing sound)
modulation = 0.5 * (1 + np.sin(2 * np.pi * ring_burst_freq * t_ring))
ring_sound = base_tone * modulation

# Smooth envelope (fade-in & fade-out to prevent popping audio)
fade_samples = int(sample_rate * 0.02)  # 20ms fade
envelope = np.ones_like(ring_sound)
envelope[:fade_samples] = np.linspace(0, 1, fade_samples)
envelope[-fade_samples:] = np.linspace(1, 0, fade_samples)
ring_sound = ring_sound * envelope

# Generate 2 seconds silence
pause_sound = np.zeros(int(sample_rate * pause_duration))

# Combine Ring + Silence into 1 cycle
one_cycle = np.concatenate((ring_sound, pause_sound))

# Repeat cycles
full_audio = np.tile(one_cycle, total_cycles)

# Normalize to 16-bit PCM format
full_audio = np.int16(full_audio / np.max(np.abs(full_audio)) * 32767)

# Save .wav audio file
wavfile.write(file_name, sample_rate, full_audio)

print(f"Incoming Ringtone ready! Saved as '{file_name}'")