"""PHX-114 local voice engines for UTA (ADR-105 to ADR-109).

Concrete adapters behind the PHX-060 to PHX-064 contracts, plus the live
pipeline that joins them: faster-whisper wake spotting and transcription on the
CPU (int8), Piper speech synthesis, sounddevice microphone capture and playback.
Everything runs offline from model files in the voice models directory; nothing
here downloads a model (``scripts/voice_setup.py`` is the explicit setup step).

Importing this package loads no engine: faster-whisper, Piper, sounddevice and
numpy are imported only when a model is loaded or a device is opened. Voice is
an interface channel: the pipeline submits text through an injected responder
and holds no permission, routing or mission authority (INV-001, INV-016).
"""
