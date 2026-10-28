.PHONY: api api-local api-comfy api-tailnet comfy browser-install test build-ios app smoke model engine-test

COMFYUI_DIR ?= $(abspath ../ComfyUI)
TAILNET_OLLAMA_HOST ?=
TAILNET_OLLAMA_MODEL ?= llama3.1:8b

api-tailnet:
	GENERATION_PROVIDER=ollama OLLAMA_URL="http://$(TAILNET_OLLAMA_HOST):11434" OLLAMA_TRUSTED_HOST="$(TAILNET_OLLAMA_HOST)" OLLAMA_MODEL="$(TAILNET_OLLAMA_MODEL)" $(MAKE) api-comfy

api-local:
	GENERATION_PROVIDER=ollama $(MAKE) api-comfy

comfy:
	cd "$(COMFYUI_DIR)" && .venv/bin/python main.py --listen 127.0.0.1 --port 8188 --disable-auto-launch --disable-all-custom-nodes --offline --cache-none

api-comfy:
	IMAGE_GENERATION_PROVIDER=comfyui LOCAL_MODEL_MEMORY_SAVING=1 COMFYUI_TIMEOUT=600 COMFYUI_WORKFLOW="$(CURDIR)/backend/workflows/sdxl-lightning-api.json" $(MAKE) api

api:
	.venv/bin/python -m uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8000

browser-install:
	.venv/bin/python -m playwright install chromium

test:
	.venv/bin/python -m pytest -q

build-ios:
	xcodebuild -quiet -project native/UpToDate.xcodeproj -scheme UpToDate -destination 'generic/platform=iOS Simulator' -derivedDataPath native/DerivedData CODE_SIGNING_ALLOWED=NO build

app:
	xcodebuild -quiet -project native/UpToDate.xcodeproj -scheme UpToDate -destination 'platform=macOS' -derivedDataPath native/DerivedData build
	open native/DerivedData/Build/Products/Debug/UpToDate.app

smoke:
	.venv/bin/python scripts/smoke_api.py

model:
	ollama serve

engine-test:
	.venv/bin/python -m scripts.generate_source --output data/local-model-test.json
