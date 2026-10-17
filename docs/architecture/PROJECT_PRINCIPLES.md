# Project Principles

## Data handling

Treat recordings, transcripts, credentials, and extracted personal or corporate data as sensitive. Uploaded and live audio is sent to AssemblyAI for transcription; detected transcript PII is redacted locally before Gemini analysis and local memory indexing. Disclose the audio transfer clearly in the user interface.

## Cross-platform support

Support macOS on Apple Silicon and Windows. Use Python `pathlib` for filesystem operations. Select PyTorch hardware dynamically in implementation using the user-provided CUDA, MPS, and CPU fallback order. Document platform-specific binary installation steps, including FFmpeg, for Homebrew and winget.

## Modular monorepo

Keep backend and frontend code in their own trees and group implementation by feature domain.

## Planned stack

FastAPI, SQLAlchemy, SQLite, React, Vite, Tailwind CSS, AssemblyAI, Pydantic, LiteLLM or Instructor, ChromaDB, and edge-tts.
