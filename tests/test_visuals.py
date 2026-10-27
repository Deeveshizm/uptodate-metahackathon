import io
import json
from unittest.mock import Mock

import pymupdf
import httpx
import pytest
from PIL import Image

from backend.documents import Document, parse_document
from backend.retrieval import Resource, SourceError
from backend.visuals import ComfyIllustrator, IllustrationBrief, choose_visual, decode_image, html_figures, pdf_figures, primary_visual_urls

LICENSE = "https://creativecommons.org/licenses/by/4.0/"
SOURCE = "https://publication.example/paper"
IMAGE = "https://publication.example/architecture.png"


def png(width=400, height=300):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def setup_visual():
    doc = Document(SOURCE, "A local model pipeline", [], metadata={"author": "Example authors"}, figures=[{
        "id": "fig-1", "url": IMAGE, "caption": "Figure 1. Architecture of the local model pipeline",
        "license_url": LICENSE, "page": None,
    }])
    draft = {"title": "A local model pipeline", "finding": "The local model pipeline turns sources into verified posts."}
    fetcher, illustrator = Mock(), Mock()
    fetcher.fetch.return_value = Resource(IMAGE, "image/png", png())
    illustrator.generate.return_value = png()
    return doc, draft, fetcher, illustrator


def test_source_figure_prevents_image_generation():
    doc, draft, fetcher, illustrator = setup_visual()
    visual, data = choose_visual(doc, draft, fetcher, illustrator)
    assert visual["kind"] == "source"
    assert visual["license_url"] == LICENSE
    assert visual["attribution"] == "Example authors"
    assert data.startswith(b"\x89PNG")
    illustrator.generate.assert_not_called()


@pytest.mark.parametrize("license_url", ["", "https://publication.example/copyright", LICENSE])
def test_existing_source_image_never_triggers_generation_based_on_license(license_url):
    doc, draft, fetcher, illustrator = setup_visual()
    doc.figures[0]["license_url"] = license_url
    visual, data = choose_visual(doc, draft, fetcher, illustrator)
    assert visual["kind"] == "source" and visual["status"] == "ready"
    assert visual["license_url"] == license_url
    assert visual["requires_rights_review"] == (license_url != LICENSE)
    assert visual["rights_status"] == ("recognized_reuse_license" if license_url == LICENSE else "needs_review")
    assert data.startswith(b"\x89PNG")
    illustrator.generate.assert_not_called()


def test_unknown_rights_source_image_does_not_require_ollama_or_comfy(monkeypatch):
    monkeypatch.delenv("COMFYUI_WORKFLOW", raising=False)
    monkeypatch.setattr(ComfyIllustrator, "illustrate", Mock(side_effect=AssertionError("Source image must short-circuit fallback")))
    doc, draft, fetcher, _ = setup_visual()
    doc.figures[0]["license_url"] = ""
    visual, _ = choose_visual(doc, draft, fetcher)
    assert visual["kind"] == "source" and visual["status"] == "ready"
    ComfyIllustrator.illustrate.assert_not_called()


@pytest.mark.parametrize("reason", ["absent", "irrelevant", "invalid", "tiny"])
def test_fallback_only_after_no_usable_source_figure(reason):
    doc, draft, fetcher, illustrator = setup_visual()
    if reason == "absent": doc.figures = []
    if reason == "irrelevant": doc.figures[0]["caption"] = "Summer basketball tournament winners"
    if reason == "invalid": fetcher.fetch.return_value.body = b"not an image"
    if reason == "tiny": fetcher.fetch.return_value.body = png(20, 20)
    visual, data = choose_visual(doc, draft, fetcher, illustrator)
    illustrator.generate.assert_called_once()
    assert draft["finding"] in illustrator.generate.call_args.args[0]
    assert visual["kind"] == "generated" and "not a source figure" in visual["caption"]
    assert data.startswith(b"\x89PNG")


def test_unconfigured_fallback_stays_pending(monkeypatch):
    monkeypatch.delenv("COMFYUI_WORKFLOW", raising=False)
    doc, draft, fetcher, _ = setup_visual()
    doc.figures = []
    visual, image = choose_visual(doc, draft, fetcher)
    assert visual["status"] == "needs_image_generator"
    assert image is None
    assert "COMFYUI_WORKFLOW" in visual["detail"]


def test_html_figure_caption_license_and_logo_filter():
    resource = Resource(SOURCE, "text/html", b'<article><img src="/logo.png" alt="Company logo"><figure><img src="/architecture.png"><figcaption>Figure 1. Architecture of the local model pipeline <a rel="license" href="https://creativecommons.org/licenses/by/4.0/">CC BY</a></figcaption></figure></article>')
    candidates = html_figures(resource, "")
    assert len(candidates) == 1
    assert candidates[0]["url"] == IMAGE
    assert candidates[0]["license_url"] == LICENSE


def test_paper_author_credit_and_license_label_are_preserved():
    body = b'<html><title>Local model pipeline</title><a href="https://info.arxiv.org/help/license/index.html">License: CC BY-SA 4.0</a><div class="ltx_authors"><span class="ltx_personname">Alice Researcher</span><span class="ltx_personname">Bob Researcher</span></div><article><p>This original research paper explains how a local model pipeline works and reports its components with a source figure.</p><figure><img src="/figure.png"><figcaption>Figure 1. Local model pipeline architecture</figcaption></figure></article></html>'
    doc = parse_document(Resource("https://arxiv.org/html/2603.29928", "text/html", body))
    assert doc.metadata["author"] == "Alice Researcher, Bob Researcher"
    fetcher, illustrator = Mock(), Mock()
    fetcher.fetch.return_value = Resource("https://arxiv.org/figure.png", "image/png", png())
    visual, _ = choose_visual(doc, {"title": "Local model pipeline", "finding": "A source-grounded pipeline."}, fetcher, illustrator)
    assert visual["attribution"] == "Alice Researcher, Bob Researcher"
    assert visual["license_name"] == "License: CC BY-SA 4.0"
    illustrator.generate.assert_not_called()


def test_third_party_figure_does_not_inherit_page_license():
    resource = Resource(SOURCE, "text/html", b'<article><figure><img src="/architecture.png"><figcaption>Architecture overview, courtesy of another publisher</figcaption></figure></article>')
    assert html_figures(resource, LICENSE)[0]["license_url"] == ""


def test_pdf_source_page_preserves_vector_figure():
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=400, height=500)
        page.draw_rect(pymupdf.Rect(50, 50, 350, 180), color=(0, 0, 0))
        page.insert_text((60, 100), "Local model pipeline")
        page.insert_text((50, 210), "Figure 1. Architecture of the local model pipeline")
        pdf.set_xml_metadata('<metadata><license>' + LICENSE + '</license></metadata>')
        data = pdf.tobytes()
    candidates, license_url = pdf_figures(Resource(SOURCE, "application/pdf", data))
    assert license_url == LICENSE and candidates[0]["page"] == 1
    assert candidates[0]["image_data"].startswith(b"\x89PNG")
    assert "Source page" in candidates[0]["caption"]


def test_pdf_without_license_still_exposes_existing_source_figure():
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=400, height=500)
        page.draw_rect(pymupdf.Rect(50, 50, 350, 180), color=(0, 0, 0))
        page.insert_text((50, 210), "Figure 1. Architecture of the local model pipeline")
        page.insert_text((50, 240), "This paper explains the local model pipeline and its components.")
        data = pdf.tobytes()
    doc = parse_document(Resource(SOURCE, "application/pdf", data))
    assert doc.figures and not doc.figures[0]["license_url"]
    illustrator = Mock()
    visual, data = choose_visual(doc, {"title": "Local model pipeline architecture", "finding": "The source describes a local model pipeline."}, Mock(), illustrator)
    assert visual["kind"] == "source" and visual["requires_rights_review"]
    assert data.startswith(b"\x89PNG")
    illustrator.generate.assert_not_called()


def test_original_paper_figure_beats_newsletter_hero_image():
    doc, draft, fetcher, illustrator = setup_visual()
    paper = "https://arxiv.org/html/2603.29928"
    figure = paper + "/x1.png"
    doc.links = [{"url": "https://arxiv.org/abs/2603.29928?utm_source=newsletter", "title": "Original paper"}]
    body = ('<html><title>Local model pipeline</title><article><p>' + "A local model pipeline described in the original research paper. " * 3 +
            '</p><figure><img src="' + figure + '"><figcaption>Figure 1. Local model pipeline architecture</figcaption></figure></article></html>').encode()
    def fetch(url):
        if url == paper:
            return Resource(paper, "text/html", body)
        if url == figure:
            return Resource(figure, "image/png", png())
        raise AssertionError("Newsletter hero or fallback must not be fetched: " + url)
    fetcher.fetch.side_effect = fetch
    visual, data = choose_visual(doc, draft, fetcher, illustrator)
    assert visual["source_url"] == paper and visual["image_source_url"] == figure
    assert visual["source_role"] == "primary" and visual["article_url"] == doc.url
    assert visual["requires_rights_review"] and data.startswith(b"\x89PNG")
    illustrator.generate.assert_not_called()


def test_unavailable_original_uses_existing_article_image_not_generation():
    doc, draft, fetcher, illustrator = setup_visual()
    doc.links = [{"url": "https://arxiv.org/abs/2603.29928", "title": "Original paper"}]
    def fetch(url):
        if url == IMAGE:
            return Resource(IMAGE, "image/png", png())
        raise SourceError("Original source unavailable")
    fetcher.fetch.side_effect = fetch
    visual, _ = choose_visual(doc, draft, fetcher, illustrator)
    assert visual["source_role"] == "article" and visual["image_source_url"] == IMAGE
    assert len(visual["primary_source_checks"]) == 2
    illustrator.generate.assert_not_called()


def test_primary_visual_discovery_is_bounded_and_does_not_crawl_references():
    doc = Document(SOURCE, "A paper", [], links=[{"url": "https://arxiv.org/abs/2603.29928", "title": "Paper"}] * 3 +
                   [{"url": "https://arxiv.org/abs/2603.29929", "title": "Second paper"},
                    {"url": "https://arxiv.org/abs/2603.29930", "title": "Third paper"}])
    assert primary_visual_urls(doc) == ["https://arxiv.org/html/2603.29928", "https://arxiv.org/pdf/2603.29928",
                                        "https://arxiv.org/html/2603.29929", "https://arxiv.org/pdf/2603.29929"]
    doc.url = "https://arxiv.org/html/2603.29928v4"
    assert primary_visual_urls(doc) == []
    doc.url = "https://arxiv.org/abs/2603.29928v4"
    assert primary_visual_urls(doc) == ["https://arxiv.org/html/2603.29928v4", "https://arxiv.org/pdf/2603.29928v4"]


def test_image_decode_rejects_non_images():
    with pytest.raises(SourceError):
        decode_image(b"<html>not an image</html>")


def test_comfy_local_workflow_protocol(tmp_path, monkeypatch):
    workflow = tmp_path / "workflow.json"
    workflow.write_text(json.dumps({"6": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{POST_PROMPT}}"}}}))
    monkeypatch.setenv("COMFYUI_WORKFLOW", str(workflow))
    monkeypatch.setenv("COMFYUI_URL", "http://127.0.0.1:8188")
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/prompt":
            payload = json.loads(request.content)
            assert payload["prompt"]["6"]["inputs"]["text"] == "Verified subject and context"
            return httpx.Response(200, json={"prompt_id": "test-job"})
        if request.url.path == "/history/test-job":
            return httpx.Response(200, json={"test-job": {"status": {"completed": True}, "outputs": {"9": {"images": [{"filename": "test.png", "subfolder": "", "type": "output"}]}}}})
        if request.url.path == "/view":
            return httpx.Response(200, content=png())
        return httpx.Response(404)
    original_client = httpx.Client
    monkeypatch.setattr("backend.visuals.httpx.Client", lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    assert ComfyIllustrator().generate("Verified subject and context").startswith(b"\x89PNG")
    assert calls == ["/prompt", "/history/test-job", "/view"]


def test_comfy_rejects_remote_endpoints(tmp_path, monkeypatch):
    workflow = tmp_path / "workflow.json"
    workflow.write_text('{}')
    monkeypatch.setenv("COMFYUI_WORKFLOW", str(workflow))
    monkeypatch.setenv("COMFYUI_URL", "https://remote.example")
    with pytest.raises(SourceError, match="loopback"):
        ComfyIllustrator().generate("test")


def test_post_illustration_uses_short_scene_not_technical_article(monkeypatch):
    monkeypatch.setenv("COMFYUI_WORKFLOW", "configured-workflow.json")
    model = Mock()
    model.complete.return_value = IllustrationBrief(scene="A simple balance scale holding a smooth pebble and a translucent cloud")
    illustrator = ComfyIllustrator()
    illustrator._generate = Mock(return_value=png())
    stages = []
    draft = {"title": "ScoringBench research rankings", "finding": "Scores evaluate prediction uncertainty and accuracy."}
    prompt, data = illustrator.illustrate(draft, "Muted blue and green on ivory", stages.append, model)
    assert model.complete.call_args.args[2] == {"title": draft["title"], "verified_post": {"summary": draft["finding"]}, "evidence": []}
    assert "balance scale" in prompt
    assert draft["title"] not in prompt and draft["finding"] not in prompt
    assert prompt.startswith("Minimal wordless editorial illustration.")
    illustrator._generate.assert_called_once_with(prompt)
    assert data.startswith(b"\x89PNG")
    assert stages == ["planning_fallback_visual", "generating_fallback_visual"]


def test_technical_visual_planner_receives_full_verified_content(monkeypatch):
    monkeypatch.setenv("COMFYUI_WORKFLOW", "configured-workflow.json")
    analysis = {"content_type": "paper", "problem": "Manual sorting", "approach": "A filter separates incoming pebbles into two trays.",
                "finding": "The source describes the separation process.", "impact": None,
                "verification": {"internal": "Do not include model bookkeeping"}}
    evidence = [{"field": "approach", "quote": analysis["approach"], "span_id": "s001"}]
    draft = {"title": "A sorting method", "finding": "A filter sorts pebbles.", "analysis": analysis, "evidence": evidence}
    model = Mock()
    model.complete.return_value = IllustrationBrief(visual_type="process", scene="A funnel of small pebbles above a filter leading into two plain trays")
    illustrator = ComfyIllustrator()
    illustrator._generate = Mock(return_value=png())
    prompt, _ = illustrator.illustrate(draft, "Muted colors", model=model)
    payload = model.complete.call_args.args[2]
    assert payload["verified_post"]["approach"] == analysis["approach"]
    assert payload["verified_post"]["problem"] == analysis["problem"]
    assert "verification" not in payload["verified_post"] and "impact" not in payload["verified_post"]
    assert payload["evidence"] == evidence
    assert prompt.startswith("Minimal wordless process illustration.")


def test_process_requires_reported_approach(monkeypatch):
    monkeypatch.setenv("COMFYUI_WORKFLOW", "configured-workflow.json")
    model = Mock()
    model.complete.return_value = IllustrationBrief(visual_type="process", scene="A funnel leading into a pair of plain trays")
    illustrator = ComfyIllustrator()
    illustrator._generate = Mock()
    with pytest.raises(SourceError, match="scene needs revision"):
        illustrator.illustrate({"title": "Research", "finding": "No reported method"}, "Muted colors", model=model)
    illustrator._generate.assert_not_called()


def test_scene_repair_receives_rejected_description(monkeypatch):
    monkeypatch.setenv("COMFYUI_WORKFLOW", "configured-workflow.json")
    rejected = "A scale representing model leaderboards with scientific charts"
    scene = "A balance scale holding a smooth pebble opposite a soft green cloud"
    model = Mock()
    model.complete.side_effect = [IllustrationBrief(scene=rejected), IllustrationBrief(scene=scene)]
    illustrator = ComfyIllustrator()
    illustrator._generate = Mock(return_value=png())
    prompt, _ = illustrator.illustrate({"title": "Research", "finding": "Grounded summary"}, "Muted colors", model=model)
    assert model.complete.call_count == 2
    assert model.complete.call_args.args[2]["previous_attempt"]["scene"] == rejected
    assert scene in prompt and rejected not in prompt
    illustrator._generate.assert_called_once_with(prompt)


@pytest.mark.parametrize("scene", ["A scientific chart with fake model rankings", "A grid of numerical results and labels", "A balance scale holding 50 pebbles", "A " + "stone " * 41])
def test_technical_or_overlong_scene_cannot_reach_diffusion(monkeypatch, scene):
    monkeypatch.setenv("COMFYUI_WORKFLOW", "configured-workflow.json")
    model = Mock()
    model.complete.return_value = IllustrationBrief(scene=scene)
    illustrator = ComfyIllustrator()
    illustrator._generate = Mock()
    with pytest.raises(SourceError, match="scene needs revision"):
        illustrator.illustrate({"title": "Research", "finding": "Grounded summary"}, "Muted colors", model=model)
    illustrator._generate.assert_not_called()


def test_comfy_memory_saving_releases_models(tmp_path, monkeypatch):
    workflow = tmp_path / "workflow.json"
    workflow.write_text(json.dumps({"6": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{POST_PROMPT}}"}}}))
    monkeypatch.setenv("COMFYUI_WORKFLOW", str(workflow))
    monkeypatch.setenv("LOCAL_MODEL_MEMORY_SAVING", "1")
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "job"})
        if request.url.path == "/history/job":
            return httpx.Response(200, json={"job": {"outputs": {"9": {"images": [{"filename": "test.png", "type": "output"}]}}}})
        if request.url.path == "/view":
            return httpx.Response(200, content=png())
        assert request.url.path == "/free"
        assert json.loads(request.content) == {"unload_models": True, "free_memory": True}
        return httpx.Response(200)
    original_client = httpx.Client
    monkeypatch.setattr("backend.visuals.httpx.Client", lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    assert ComfyIllustrator().generate("test").startswith(b"\x89PNG")
    assert calls[-1] == "/free"


def test_comfy_timeout_is_bounded(tmp_path, monkeypatch):
    workflow = tmp_path / "workflow.json"
    workflow.write_text('{"prompt": "{{POST_PROMPT}}"}')
    monkeypatch.setenv("COMFYUI_WORKFLOW", str(workflow))
    monkeypatch.setenv("COMFYUI_TIMEOUT", "nan")
    with pytest.raises(SourceError, match="COMFYUI_TIMEOUT"):
        ComfyIllustrator().generate("test")


def test_bundled_lightning_workflow():
    from pathlib import Path
    workflow = json.loads((Path(__file__).parents[1] / "backend/workflows/sdxl-lightning-api.json").read_text())
    assert workflow["4"]["inputs"]["ckpt_name"] == "sdxl_lightning_4step.safetensors"
    sampler = workflow["3"]["inputs"]
    assert (sampler["steps"], sampler["cfg"], sampler["sampler_name"], sampler["scheduler"]) == (4, 1, "euler", "sgm_uniform")
    assert workflow["6"]["inputs"]["text"] == "{{POST_PROMPT}}"
    for node in workflow.values():
        for value in node["inputs"].values():
            if isinstance(value, list):
                assert value[0] in workflow
