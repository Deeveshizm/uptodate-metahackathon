(() => {
  const safeLink = value => {
    try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : null; }
    catch { return null; }
  };
  const link = (label, url, className = "") => {
    const target = safeLink(url);
    const node = document.createElement(target ? "a" : "span");
    node.textContent = label;
    if (target) { node.href = target; node.rel = "noreferrer noopener"; }
    node.className = className;
    return node;
  };
  async function loadImage(figure) {
    if (figure.dataset.loaded) return;
    figure.dataset.loaded = "1";
    const status = figure.querySelector(".image-status");
    try {
      const response = await fetch(figure.dataset.image, { credentials: "omit", cache: "no-store", headers: { "X-UpToDate-Photo-Attribution": "1" } });
      if (!response.ok) throw Error("Image unavailable");
      const attribution = response.headers.get("X-UpToDate-Photo-Credit");
      if (figure.dataset.google === "true" && !attribution) throw Error("Image attribution unavailable");
      if (attribution) {
        const bytes = Uint8Array.from(atob(attribution), character => character.charCodeAt(0));
        const credit = JSON.parse(new TextDecoder().decode(bytes));
        const container = figure.querySelector(".photo-credit");
        const brand = link("Google Maps", credit.source_url, "google-credit");
        brand.setAttribute("translate", "no"); container.append(brand);
        for (const author of credit.authors || []) if (typeof author.displayName === "string") container.append(link(author.displayName, author.uri));
      }
      const blob = await response.blob();
      const image = figure.querySelector("img");
      image.src = URL.createObjectURL(blob); image.hidden = false; status.hidden = true;
      image.addEventListener("load", () => URL.revokeObjectURL(image.src), { once: true });
    } catch { status.textContent = "Image unavailable. The original source is linked below."; }
  }
  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(entries => { for (const entry of entries) if (entry.isIntersecting) { loadImage(entry.target); observer.unobserve(entry.target); } }, { rootMargin: "200px" });
    document.querySelectorAll("figure[data-image]").forEach(figure => observer.observe(figure));
  } else document.querySelectorAll("figure[data-image]").forEach(loadImage);
  document.querySelectorAll("[data-venue]").forEach(async section => {
    try {
      const response = await fetch(section.dataset.venue, { credentials: "omit", cache: "no-store" });
      if (!response.ok) return;
      const value = await response.json(); const list = document.createElement("dl"); list.className = "facts";
      for (const [name, text] of Object.entries(value.fields || {})) {
        if (typeof text !== "string") continue;
        const title = document.createElement("dt"); title.textContent = name[0].toUpperCase() + name.slice(1);
        const detail = document.createElement("dd"); detail.textContent = text; list.append(title, detail);
      }
      if (!list.children.length) return;
      const brand = link("Google Maps", value.source_url, "google-credit"); brand.setAttribute("translate", "no");
      section.append(brand, list); section.hidden = false;
    } catch { /* Source-provided post content remains readable. */ }
  });
})();
