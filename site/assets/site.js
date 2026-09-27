const fmt = new Intl.NumberFormat();

function fraction(value) {
  if (!value || typeof value.numerator !== "number" || typeof value.denominator !== "number") return "not reported";
  return (value.numerator / value.denominator).toExponential(3);
}

function bytes(value) {
  if (typeof value !== "number") return "not reported";
  if (value >= 1024 * 1024) return (value / (1024 * 1024)).toFixed(1) + " MiB";
  return fmt.format(value) + " B";
}

function text(tag, value, className) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  el.textContent = value;
  return el;
}

function metric(label, value) {
  const el = document.createElement("div");
  el.className = "metric";
  el.append(text("strong", label), text("span", value));
  return el;
}

function section(title, value) {
  const el = document.createElement("div");
  el.append(text("h4", title), text("p", value || "Not declared."));
  return el;
}

(async () => {
  const host = document.getElementById("catalog");
  try {
    const response = await fetch("data/experiments.json", {cache: "no-store"});
    if (!response.ok) throw new Error("catalog unavailable");
    const data = await response.json();
    host.replaceChildren();

    for (const exp of data.experiments || []) {
      const article = document.createElement("article");
      article.className = "experiment";

      const head = document.createElement("div");
      head.className = "experiment-head";
      const copy = document.createElement("div");
      copy.append(text("h3", exp.title), text("p", exp.hypothesis || exp.question || "", "hypothesis"));

      const meta = document.createElement("div");
      meta.className = "metrics";
      const env = exp.environment || {};
      meta.append(
        metric("Package", bytes(exp.package && exp.package.size)),
        metric("Protocol", exp.lifecycle && exp.lifecycle.creativeWorkStatus || "published reproduction"),
        metric("Attempts", exp.lifecycle ? fmt.format(exp.lifecycle.attemptCount) : "reference evidence"),
        metric("Network", env.networkRequired === false ? "none" : String(env.networkRequired)),
        metric("VRAM", bytes(exp.resources && exp.resources.accelerator && exp.resources.accelerator.peakVramBytes)),
        metric("Planning RAM", bytes(exp.resources && exp.resources.ram && exp.resources.ram.planningRamBytes))
      );

      head.append(copy, meta);

      const result = exp.result || {};
      const details = document.createElement("div");
      details.className = "details";
      const evidenceText = exp.scientificInterpretation === null
        ? "No scientific interpretation is recorded in this plan."
        : "Held-out MSE " + fraction(result.metrics && result.metrics.evalMse) + "; acceptance " + (result.acceptancePassed ? "passed" : "not passed") + ".";
      details.append(
        section("Scientific question", exp.question),
        section("Method", exp.method),
        section("Evidence", evidenceText),
        section("Scientific interpretation", exp.scientificInterpretation),
        section("Environment", (env.python || "Python") + "; " + (env.standardLibraryOnly ? "standard library only" : "dependencies declared") + "; accelerator " + (env.accelerator || "not reported") + "."),
        section("Reproduce", exp.reproduction && exp.reproduction.entrypoint ? "Run " + exp.reproduction.entrypoint + " after extraction." : "Entrypoint not declared."),
        section("Provenance", (exp.source && exp.source.repository ? exp.source.repository : "") + "@" + (exp.source && exp.source.commit ? exp.source.commit : ""))
      );

      const actions = document.createElement("div");
      actions.className = "actions";
      if (exp.package && exp.package.sha256) {
        const download = document.createElement("a");
        download.className = "primary";
        download.href = "packages/" + exp.package.sha256 + ".zip";
        download.download = "";
        download.textContent = "Download Compiled Experiment ↓";
        actions.append(download);
      }
      const source = document.createElement("a");
      source.href = "https://github.com/" + exp.source.repository + "/tree/" + exp.source.commit;
      source.textContent = "Frozen source ↗";
      actions.append(source);

      article.append(head, details, actions);
      host.append(article);
    }
  } catch (error) {
    host.textContent = "Compiled Experiment catalog unavailable.";
  }
})();
