# Starting point

This repository's initial snapshot was prepared on October 26, 2026 from the existing UpToDate prototype at private development commit `61252142698f39a61db4ac454b9be44334fa3c3c`. Publishing this snapshot does not represent the existing implementation as new hackathon work.

The baseline includes the SwiftUI iPhone/Mac client, local account sessions and placeholder aliases, offline EML import, newsletter-link discovery, original-source retrieval, Ollama drafting and verification, source-first images with optional local ComfyUI fallback, an automatic private feed, bookmarks, and job status. Supabase SQL is preparatory; the runtime uses SQLite. Real email receiving, recoverable user authentication, and live newsletter signup are not implemented in this baseline.

The starting implementation was built collaboratively by Deeveshizm and Ujwal Jain, as recorded in the original Git commit authors, with coding-agent assistance. UI and backend work originated in the private development repository. This public snapshot has independent history; original development history remains in the private repository.

Public packaging changes remove machine-specific connection defaults, add ignore rules, and clarify setup. App behavior and content-generation logic are unchanged. Secrets, user data, local environments, generated artifacts, model weights, and build caches are excluded.

## Work completed during the hackathon

Record tested milestones here as they are published. Hosted inference has not been activated; the baseline is `~local generation only`.

## Baseline verification

Checked from this checkout on October 26, 2026:

- A separate Python virtual environment installed successfully from `requirements.txt`; `pip check` found no broken dependencies.
- `make test`: 114 tests passed, with dependency deprecation warnings.
- `make build-ios`: simulator build succeeded.
- `make smoke`: live local account creation, EML import, automatic private feed, bookmarks, and source sharing passed. The test deleted only its temporary account.
- `python -m scripts.generate_source --output data/baseline-generation-check.json`: real local `llama3:8b` evidence selection, drafting, and verification passed against the bundled fixture. The generated artifact stays ignored.

These checks do not verify real email delivery or subscription, hosted Supabase, or cloud inference. Source-image paths have automated coverage; ComfyUI fallback generation was not rerun for this packaging check. Machine verification does not establish factual accuracy.
