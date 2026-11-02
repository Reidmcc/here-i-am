"""Code shared by the local voice servers (XTTS, StyleTTS 2, Whisper).

Imported by the servers only, never by the backend app: the servers run on
the machine with the GPU, and the browser on that machine calls them
directly (issue #399).
"""
